"""Semantic tools for Nexo's private, bounded long-term memory."""

from __future__ import annotations

import threading
from typing import Any, Mapping, Sequence

from .memory import MemoryStore


_STORE: MemoryStore | None = None
_STORE_LOCK = threading.Lock()


def get_memory_store() -> MemoryStore:
    """Return the process-wide memory store, creating it lazily."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = MemoryStore()
        return _STORE


def remember_preference(key: str, value: str) -> dict[str, Any]:
    """Persist a preference the user stated explicitly."""
    stored = get_memory_store().remember_preference(
        key=key,
        value=value,
        source="user",
        confidence=1.0,
        confirmed=True,
    )
    return {"ok": bool(stored), "key": str(key), "stored": bool(stored)}


def recall_preferences(query: str = "", limit: int = 5) -> dict[str, Any]:
    """Recall explicit user preferences without exposing unrelated history."""
    preferences = get_memory_store().search_preferences(query=query, limit=limit)
    return {
        "ok": True,
        "query": str(query),
        "count": len(preferences),
        "preferences": preferences,
    }


def forget_preference(key: str) -> dict[str, Any]:
    """Forget exactly one saved preference."""
    removed = get_memory_store().forget_preference(key)
    return {"ok": True, "key": str(key), "removed": bool(removed)}


def record_agent_receipt(
    *,
    task_id: str,
    status: str,
    steps: Sequence[Mapping[str, Any]] = (),
    verified: bool = False,
    store: MemoryStore | None = None,
) -> None:
    """Record execution metadata only; never persist prompts or responses."""
    tool_names = [
        str(step.get("name") or "")[:96]
        for step in steps
        if isinstance(step, Mapping) and step.get("name")
    ][:16]
    (store or get_memory_store()).record_task_receipt(
        "agent_task",
        status=str(status)[:64] or "unknown",
        task_id=str(task_id)[:160],
        metadata={
            "tools": tool_names,
            "step_count": len(tool_names),
            "verified": bool(verified),
        },
    )


MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remember_preference",
            "description": (
                "Guarda uma preferência pessoal que o usuário afirmou explicitamente, "
                "como música, aplicativo ou modo de resposta favorito. Não use para "
                "inferências, segredos, credenciais, conversas ou dados temporários."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Chave semântica curta e estável, como musica_favorita.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Valor afirmado explicitamente pelo usuário.",
                    },
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_preferences",
            "description": (
                "Consulta preferências pessoais explicitamente salvas. Use antes de "
                "agir sobre expressões como meu favorito, do jeito que eu gosto ou "
                "minha preferência."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Assunto ou chave da preferência; vazio lista as mais recentes.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_preference",
            "description": "Apaga exatamente uma preferência pessoal salva, após confirmação.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Chave exata da preferência a esquecer.",
                    },
                },
                "required": ["key"],
                "additionalProperties": False,
            },
        },
    },
]
