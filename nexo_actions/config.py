"""Central, user-editable configuration for Nexo."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


CMD = "/mnt/c/Windows/System32/cmd.exe"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "nexo" / "config.json"


DEFAULT_CONFIG: dict[str, Any] = {
    "ai": {
        "provider": "openai",
        "fallback_provider": "ollama",
        "fallback_enabled": False,
        "profiles": {
            "openai": {
                "agent_model": "gpt-5.6-sol",
                "realtime_model": "gpt-realtime-2.1",
                "voice": "marin",
                # Actions favor reliability; realtime voice remains latency
                # oriented and does not use this setting directly.
                "reasoning_effort": "medium",
                "agent_text_verbosity": "low",
                # Routine Realtime delegations clone the provider with this
                # lower effort; the configured effort above remains untouched
                # for programming, research and multi-step action plans.
                "routine_reasoning_effort": "none",
                # Empty keeps the selected agent model. Advanced users may set
                # a faster compatible model without changing complex tasks.
                "routine_agent_model": "gpt-5.6-terra",
                # The live conversational model should react immediately; the
                # Responses worker still raises effort for complex delegated
                # work. Both settings are exposed in ADM.
                "realtime_reasoning_effort": "minimal",
                "realtime_max_output_tokens": 1024,
                "realtime_response_style": "concise",
            },
            "ollama": {
                "url": "http://localhost:11434/api/chat",
                "model": "gemma4:12b",
                "keep_alive": "30m",
            },
        },
    },
    # Kept in sync by the ADM service while older runtime components migrate to
    # the provider-neutral ``ai`` block above.
    "ollama": {
        "url": "http://localhost:11434/api/chat",
        "model": "gemma4:12b",
        "keep_alive": "30m",
    },
    "conversation": {
        "timeout_seconds": 180,
        "max_messages": 24,
        "max_tokens": 2200,
        "barge_in": True,
        "barge_in_requires_wakeword": True,
        "echo_guard": True,
        "echo_window_seconds": 8,
        # Volume ducking starts before STT knows what was said. Keep it at the
        # normal level; the skill stops speech after recognizing "Nexo".
        "barge_in_volume_percent": 100,
    },
    "audio": {
        # Software gain is applied after WebRTC echo cancellation.  Keeping it
        # here makes the level survive USB capture-card reconnects.
        "input_gain_percent": 135,
        "auto_recover": True,
        "health_interval_seconds": 5,
        "failure_threshold": 3,
    },
    "agent": {
        # Cross-app tasks (for example Codex -> project -> task submission)
        # need enough room for the observe -> act -> verify cycle without
        # turning the tool loop into an unbounded desktop session.
        "max_steps": 12,
        "timeout_seconds": 180,
    },
    "site_aliases": {
        "clube envios": "https://clubeenvios.com.br",
        "club envios": "https://clubeenvios.com.br",
        "clubenvios": "https://clubeenvios.com.br",
        "melhor envio": "https://melhorenvio.com.br",
        "google": "https://google.com",
        "youtube": "https://youtube.com",
    },
    "app_aliases": {
        "vscode": "visual studio code",
        "vs code": "visual studio code",
        "chrome": "google chrome",
        "explorador": "explorador de arquivos",
        "bloco de notas": "notepad",
    },
    "known_directories": {},
    "bridge": {
        "url": "http://127.0.0.1:8765",
        "token_path": "%LOCALAPPDATA%\\Nexo\\bridge-token",
        "timeout_seconds": 8,
    },
    "email": {"providers": {}},
    "ui": {"host": "127.0.0.1", "port": 8787, "open_on_start": False},
    "safety": {
        "confirmation_timeout_seconds": 60,
        "confirm_medium_risk": False,
    },
    "logging": {"level": "INFO"},
}


_LOCK = threading.RLock()
_CACHE: dict[str, Any] | None = None
_CACHE_MTIME: int | None = None
_WINDOWS_PROFILE: str | None = None


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def windows_user_profile() -> str:
    """Return the current interactive Windows profile without hard-coding it."""
    global _WINDOWS_PROFILE
    with _LOCK:
        if _WINDOWS_PROFILE:
            return _WINDOWS_PROFILE
        env_value = os.environ.get("NEXO_WINDOWS_PROFILE")
        if env_value:
            _WINDOWS_PROFILE = env_value.rstrip("\\/")
            return _WINDOWS_PROFILE
        try:
            process = subprocess.run(
                [CMD, "/d", "/c", "echo %USERPROFILE%"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                cwd="/mnt/c",
            )
            value = process.stdout.strip().lstrip("\ufeff")
            if process.returncode == 0 and re.match(r"^[A-Za-z]:\\", value):
                _WINDOWS_PROFILE = value.rstrip("\\")
                return _WINDOWS_PROFILE
        except Exception:
            pass

        # Conservative discovery fallback for WSL installations.
        users = Path("/mnt/c/Users")
        candidates = [
            item for item in users.iterdir()
            if item.is_dir()
            and item.name.casefold() not in {"public", "default", "default user", "all users"}
            and (item / "AppData").exists()
        ] if users.exists() else []
        if len(candidates) == 1:
            _WINDOWS_PROFILE = f"C:\\Users\\{candidates[0].name}"
            return _WINDOWS_PROFILE
        raise RuntimeError("Não foi possível descobrir o perfil do usuário Windows")


def windows_to_wsl(path: str) -> Path:
    path = os.path.expandvars(str(path).strip().strip('"'))
    unc = re.match(
        r"^\\\\wsl(?:\.localhost|\$)\\[^\\]+\\(.*)$",
        path,
        flags=re.I,
    )
    if unc:
        parts = [part for part in re.split(r"[\\/]+", unc.group(1)) if part]
        return Path("/") / Path(*parts)
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if not match:
        return Path(path).expanduser()
    drive, rest = match.groups()
    parts = [part for part in re.split(r"[\\/]+", rest) if part]
    return Path("/mnt") / drive.casefold() / Path(*parts)


def wsl_to_windows(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve(strict=False)
    parts = resolved.parts
    if len(parts) >= 4 and parts[1] == "mnt" and len(parts[2]) == 1:
        tail = "\\".join(parts[3:])
        return f"{parts[2].upper()}:\\{tail}"
    if str(resolved).startswith("/home/"):
        distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
        tail = str(resolved).replace("/", "\\").lstrip("\\")
        return f"\\\\wsl.localhost\\{distro}\\{tail}"
    raise ValueError(f"Caminho não pode ser convertido para Windows: {path}")


def known_directories(config: Mapping[str, Any] | None = None) -> dict[str, str]:
    profile = windows_user_profile()
    defaults = {
        "profile": profile,
        "downloads": profile + "\\Downloads",
        "desktop": profile + "\\Desktop",
        "documents": profile + "\\Documents",
        "nexo": wsl_to_windows(Path(__file__).resolve().parents[1]),
    }
    supplied = dict((config or load_config()).get("known_directories", {}))
    defaults.update({str(key).casefold(): str(value) for key, value in supplied.items()})
    return defaults


def expand_windows_env(path: str) -> str:
    profile = windows_user_profile()
    replacements = {
        "%USERPROFILE%": profile,
        "%LOCALAPPDATA%": profile + "\\AppData\\Local",
        "%APPDATA%": profile + "\\AppData\\Roaming",
    }
    result = str(path)
    for key, value in replacements.items():
        result = re.sub(re.escape(key), lambda _match: value, result, flags=re.I)
    return result


def load_config(path: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    global _CACHE, _CACHE_MTIME
    path = Path(path or os.environ.get("NEXO_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()
    with _LOCK:
        mtime = path.stat().st_mtime_ns if path.exists() else None
        if not force and _CACHE is not None and _CACHE_MTIME == mtime:
            return deepcopy(_CACHE)
        override: Mapping[str, Any] = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, Mapping):
                raise ValueError("A configuração raiz do Nexo deve ser um objeto JSON")
            override = loaded
        _CACHE = _deep_merge(DEFAULT_CONFIG, override)
        _CACHE_MTIME = mtime
        return deepcopy(_CACHE)


def ensure_config(path: Path | None = None) -> Path:
    """Create an editable config once, preserving any existing file."""
    path = Path(path or DEFAULT_CONFIG_PATH).expanduser()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    initial = _deep_merge(
        DEFAULT_CONFIG,
        {"known_directories": known_directories(DEFAULT_CONFIG)},
    )
    with path.open("x", encoding="utf-8") as handle:
        json.dump(initial, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    path.chmod(0o600)
    return path
