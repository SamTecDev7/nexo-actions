#!/usr/bin/env python3
"""Migrate an existing Nexo installation to the OpenAI-first profile safely."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

from nexo_actions.admin import AdminService
from nexo_actions.config import DEFAULT_CONFIG_PATH, ensure_config
from nexo_actions.secrets import openai_credential_status


def _already_migrated(path: Path) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return False
    return isinstance(value, dict) and isinstance(value.get("ai"), dict)


def _apply_recommended_agent_limits(path: Path) -> None:
    """Persist bounded cross-app limits without replacing user configuration."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise RuntimeError("Não foi possível ler a configuração do Nexo") from exc
    if not isinstance(value, dict):
        raise RuntimeError("A configuração raiz do Nexo deve ser um objeto JSON")

    agent = value.setdefault("agent", {})
    if not isinstance(agent, dict):
        raise RuntimeError("A configuração do agente deve ser um objeto JSON")
    agent.update({"max_steps": 12, "timeout_seconds": 180})

    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def _prepare_settings(
    settings: dict,
    *,
    already_migrated: bool,
    initialize_only: bool,
    recommended: bool,
) -> bool:
    """Apply fresh-install defaults and return whether user choices were preserved."""
    preserve_configuration = bool(initialize_only and already_migrated)
    if not preserve_configuration:
        settings["ai"]["provider"] = "openai"
        settings["ai"]["fallback_provider"] = "ollama"
        settings["ai"]["fallback_enabled"] = False
    if recommended and not preserve_configuration:
        settings["ai"]["provider"] = "openai"
        settings["ai"]["fallback_enabled"] = False
        settings["ai"]["profiles"]["openai"].update({
            "agent_model": "gpt-5.6-sol",
            "realtime_model": "gpt-realtime-2.1",
            "voice": "marin",
            "reasoning_effort": "medium",
        })
    return preserve_configuration


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--if-configured",
        action="store_true",
        help="Exit successfully when OPENAI_API_KEY is not configured.",
    )
    parser.add_argument(
        "--initialize-only",
        action="store_true",
        help="Preserve a provider choice already saved through the ADM panel.",
    )
    parser.add_argument(
        "--recommended",
        action="store_true",
        help="Select the maximum-quality recommended OpenAI profile.",
    )
    arguments = parser.parse_args()

    already_migrated = _already_migrated(DEFAULT_CONFIG_PATH)
    config_path = ensure_config()

    service = AdminService(config_path=DEFAULT_CONFIG_PATH)
    try:
        settings = service.settings()
        preserve_configuration = _prepare_settings(
            settings,
            already_migrated=already_migrated,
            initialize_only=arguments.initialize_only,
            recommended=arguments.recommended,
        )

        credential = openai_credential_status()
        if (
            settings["ai"]["provider"] == "openai"
            and not credential.get("configured")
        ):
            output = {"ok": False, "changed": False, "reason": "credential_missing"}
            print(json.dumps(output))
            return 0 if arguments.if_configured else 2

        submission = service.submit_apply(settings)
        deadline = time.monotonic() + 100
        snapshot = service.jobs.get(submission.job_id)
        while snapshot and snapshot["state"] in {"pending", "running"}:
            if time.monotonic() >= deadline:
                print(json.dumps({"ok": False, "reason": "timeout"}))
                return 1
            time.sleep(0.2)
            snapshot = service.jobs.get(submission.job_id)
        if not snapshot or snapshot["state"] != "completed":
            print(json.dumps({
                "ok": False,
                "reason": (snapshot or {}).get("error_type") or "apply_failed",
                "message": (snapshot or {}).get("error") or "Aplicação não concluída",
            }, ensure_ascii=False))
            return 1
        result = snapshot.get("result") or {}
        if arguments.recommended and not preserve_configuration:
            _apply_recommended_agent_limits(config_path)
        print(json.dumps({
            "ok": True,
            "changed": True,
            "provider": settings["ai"]["provider"],
            "agent": {
                "max_steps": 12,
                "timeout_seconds": 180,
            } if arguments.recommended else None,
            "restarted": result.get("restarted") or [],
            "listening_prompt": result.get("listening_prompt") or "",
        }, ensure_ascii=False))
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
