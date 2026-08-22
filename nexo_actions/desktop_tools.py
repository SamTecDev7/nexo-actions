"""Typed desktop/window tools backed by the authenticated Windows bridge."""

from __future__ import annotations

from typing import Any

from .windows_bridge import WindowsBridgeClient


def _client() -> WindowsBridgeClient:
    return WindowsBridgeClient()


def bridge_health() -> dict[str, Any]:
    return {"ok": True, "tool": "bridge_health", **_client().health()}


def list_windows() -> dict[str, Any]:
    windows = _client().call("list_windows")
    return {"ok": True, "tool": "list_windows", "windows": windows, "count": len(windows)}


def active_window() -> dict[str, Any]:
    window = _client().call("active_window")
    return {"ok": True, "tool": "active_window", "window": window}


def focus_window(query: str) -> dict[str, Any]:
    query = str(query).strip()
    if not query:
        raise ValueError("Informe a janela que deve receber foco")
    window = _client().call("focus_window", {"query": query})
    return {"ok": True, "tool": "focus_window", "window": window}


def window_action(action: str, query: str | None = None) -> dict[str, Any]:
    if action not in {"focus", "minimize", "maximize", "restore", "close"}:
        raise ValueError("Ação de janela inválida")
    result = _client().call("window_action", {"query": query, "action": action})
    return {"ok": True, "tool": "window_action", **result}


def show_desktop() -> dict[str, Any]:
    result = _client().call("show_desktop")
    return {"ok": True, "tool": "show_desktop", **result}


def switch_window(query: str = "") -> dict[str, Any]:
    if str(query).strip():
        window = _client().call("focus_window", {"query": str(query).strip()})
        return {"ok": True, "tool": "switch_window", "mode": "focus", "window": window}
    result = _client().call("switch_window")
    return {"ok": True, "tool": "switch_window", "mode": "next", **result}


def type_text(text: str, window: str | None = None) -> dict[str, Any]:
    result = _client().call("type_text", {"text": text, "window": window})
    return {"ok": True, "tool": "type_text", **result}


DESKTOP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bridge_health",
            "description": "Verifica se o companion local do Windows está funcionando.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_windows",
            "description": "Lista janelas visíveis abertas no desktop Windows.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "active_window",
            "description": "Consulta qual janela está atualmente em primeiro plano.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "Traz uma janela ou aplicativo existente para frente.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_action",
            "description": "Minimiza, maximiza, restaura, foca ou fecha uma janela; sem query usa a janela ativa.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["focus", "minimize", "maximize", "restore", "close"]},
                    "query": {"type": "string"},
                },
                "required": ["action"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_desktop",
            "description": "Mostra a área de trabalho do Windows.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Digita texto Unicode na janela ativa ou em uma janela identificada, sem coordenadas de mouse.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "window": {"type": "string"}},
                "required": ["text"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_window",
            "description": "Alterna para a próxima janela do Windows ou traz uma janela identificada para frente.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
]
