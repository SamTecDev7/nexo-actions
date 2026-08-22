#!/usr/bin/env python3
"""Read-only health report for the complete local Nexo stack."""

from __future__ import annotations

import json
import platform
import socket
import sys
import urllib.request

from nexo_actions import TOOL_REGISTRY
from nexo_actions.config import DEFAULT_CONFIG_PATH, load_config
from nexo_actions.desktop_tools import bridge_health


def _http_json(url: str, timeout: float = 3) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def main() -> int:
    config = load_config(force=True)
    checks = {
        "python": {"ok": True, "version": platform.python_version(), "executable": sys.executable},
        "config": {"ok": DEFAULT_CONFIG_PATH.exists(), "path": str(DEFAULT_CONFIG_PATH)},
        "tools": {"ok": len(TOOL_REGISTRY.names()) >= 49, "count": len(TOOL_REGISTRY.names())},
        "messagebus": {"ok": _port_open("127.0.0.1", 8181), "address": "127.0.0.1:8181"},
    }
    try:
        checks["ollama"] = {
            "ok": True,
            "running": _http_json("http://127.0.0.1:11434/api/ps").get("models", []),
            "configured_model": config["ollama"]["model"],
        }
    except Exception as exc:
        checks["ollama"] = {"ok": False, "error": str(exc)}
    try:
        checks["windows_bridge"] = bridge_health()
    except Exception as exc:
        checks["windows_bridge"] = {"ok": False, "error": str(exc)}
    try:
        state = _http_json("http://127.0.0.1:8787/api/state")
        checks["ui"] = {"ok": True, "connected": state.get("connected"), "state": state.get("state")}
    except Exception as exc:
        checks["ui"] = {"ok": False, "error": str(exc)}

    ok = all(bool(item.get("ok")) for item in checks.values())
    print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
