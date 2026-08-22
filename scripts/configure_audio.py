#!/usr/bin/env python3
"""Keep OVOS listener routing aligned with Nexo's conversation policy."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from nexo_actions.admin import (
    LISTENING_PROMPT_VERSION,
    _atomic_write_bytes,
    _trim_listening_prompt,
)
from nexo_actions.config import load_config
from nexo_actions.stt_quality import SMART_LISTENER_SETTINGS, configure_smart_stt


DEFAULT_PATH = Path.home() / ".config" / "mycroft" / "mycroft.conf"
MISROUTING_DEMO_SKILL = "ovos-skill-hello-world.openvoiceos"


def configure(
    path: Path,
    volume_percent: int,
    *,
    barge_in: bool = True,
    requires_wakeword: bool = True,
) -> tuple[bool, Path | None]:
    """Apply a minimal, recoverable listener patch and preserve all other keys."""
    path = Path(path).expanduser()
    volume_percent = int(volume_percent)
    if not 0 <= volume_percent <= 100:
        raise ValueError("barge-in volume must be between 0 and 100 percent")

    current = {}
    original_mode = 0o600
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            raise ValueError("mycroft.conf root must be a JSON object")
        original_mode = path.stat().st_mode & 0o777

    listener = current.setdefault("listener", {})
    if not isinstance(listener, dict):
        raise ValueError("mycroft.conf listener must be a JSON object")
    stt = current.setdefault("stt", {})
    if not isinstance(stt, dict):
        raise ValueError("mycroft.conf stt must be a JSON object")
    skills = current.setdefault("skills", {})
    if not isinstance(skills, dict):
        raise ValueError("mycroft.conf skills must be a JSON object")
    blacklisted = skills.setdefault("blacklisted_skills", [])
    if not isinstance(blacklisted, list):
        raise ValueError("mycroft.conf skills.blacklisted_skills must be a list")

    prompt_changed = False
    prompt_target = None
    prompt_payload = None
    hotwords = current.get("hotwords", {})
    hotword = hotwords.get("nexo", {}) if isinstance(hotwords, dict) else {}
    if isinstance(hotword, dict):
        sound = hotword.get("sound")
        source = Path(str(sound)).expanduser() if sound else None
        if (
            source
            and source.is_file()
            and LISTENING_PROMPT_VERSION not in source.stem
        ):
            original_prompt = source.read_bytes()
            optimized_prompt = _trim_listening_prompt(original_prompt)
            if optimized_prompt != original_prompt:
                prompt_target = source.with_name(
                    f"{source.stem}-{LISTENING_PROMPT_VERSION}{source.suffix}"
                )
                prompt_payload = optimized_prompt
                hotword["sound"] = str(prompt_target)
                prompt_changed = True

    desired = {
        # The acknowledgement is real speech. Let it finish (and let WebRTC
        # settle) before looking for the user's command.
        **SMART_LISTENER_SETTINGS,
        # When interruption requires "Nexo", OVOS must not duck/stop playback
        # merely because it heard unrelated speech in the room.
        "fake_barge_in": bool(barge_in and not requires_wakeword),
        "barge_in_volume": volume_percent,
        # Full muting prevents the microphone from hearing a genuine user.
        "mute_during_output": False,
    }
    old_stt = copy.deepcopy(stt)
    configure_smart_stt(stt)
    if (
        all(listener.get(key) == value for key, value in desired.items())
        and stt == old_stt
        and MISROUTING_DEMO_SKILL in blacklisted
        and not prompt_changed
    ):
        return False, None

    backup = None
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.nexo-backup-{stamp}")
        shutil.copy2(path, backup)

    listener.update(desired)
    if MISROUTING_DEMO_SKILL not in blacklisted:
        blacklisted.append(MISROUTING_DEMO_SKILL)
    if prompt_target is not None and prompt_payload is not None:
        _atomic_write_bytes(prompt_target, prompt_payload)
    payload = json.dumps(current, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, original_mode)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return True, backup


def main() -> int:
    conversation = load_config().get("conversation", {})
    configured_default = int(conversation.get("barge_in_volume_percent", 100))
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--barge-in-volume", type=int, default=configured_default)
    args = parser.parse_args()

    changed, backup = configure(
        args.path,
        args.barge_in_volume,
        barge_in=bool(conversation.get("barge_in", True)),
        requires_wakeword=bool(conversation.get("barge_in_requires_wakeword", True)),
    )
    if changed:
        print(f"Listener OVOS ajustado em {args.path}")
        if backup:
            print(f"Backup preservado em {backup}")
    else:
        print(f"Listener OVOS já estava ajustado em {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
