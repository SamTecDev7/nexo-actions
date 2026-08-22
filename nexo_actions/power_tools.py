"""Explicit Windows session/energy tools."""

from __future__ import annotations

from .windows_bridge import WindowsBridgeClient


def lock_workstation() -> dict:
    result = WindowsBridgeClient().call("lock_workstation")
    return {"ok": True, "tool": "lock_workstation", **result}


def power_action(action: str) -> dict:
    if action not in {"sleep", "restart", "shutdown"}:
        raise ValueError("Ação de energia inválida")
    result = WindowsBridgeClient().call("power_action", {"action": action})
    return {"ok": True, "tool": "power_action", **result}


POWER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lock_workstation",
            "description": "Bloqueia imediatamente a sessão atual do Windows.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "power_action",
            "description": "Suspende, reinicia ou desliga o Windows após confirmação explícita.",
            "parameters": {
                "type": "object",
                "properties": {"action": {"type": "string", "enum": ["sleep", "restart", "shutdown"]}},
                "required": ["action"], "additionalProperties": False,
            },
        },
    },
]
