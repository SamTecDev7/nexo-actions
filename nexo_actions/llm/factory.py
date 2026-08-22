"""Build the configured provider without silently activating local fallback."""

from __future__ import annotations

from typing import Any, Mapping

from ..config import load_config
from .ollama_provider import OllamaChatClient
from .openai_provider import OpenAIResponsesClient


def create_chat_provider(config: Mapping[str, Any] | None = None):
    root = dict(config or load_config())
    ai = root.get("ai") if isinstance(root.get("ai"), Mapping) else {}
    provider = str(ai.get("provider") or "openai").casefold()
    profiles = ai.get("profiles") if isinstance(ai.get("profiles"), Mapping) else {}
    if provider == "openai":
        selected = profiles.get("openai") if isinstance(profiles.get("openai"), Mapping) else {}
        return OpenAIResponsesClient(
            model=str(selected.get("agent_model") or ai.get("model") or "gpt-5.6-sol"),
            reasoning_effort=str(selected.get("reasoning_effort") or "low"),
            text_verbosity=str(selected.get("agent_text_verbosity") or "low"),
            web_search=bool(selected.get("web_search", True)),
            store=bool(selected.get("store_responses", True)),
            max_output_tokens=int(selected.get("max_output_tokens", 12000)),
        )
    if provider not in {"ollama", "local"}:
        raise ValueError(f"Provedor de IA não suportado: {provider}")
    selected = profiles.get("ollama") if isinstance(profiles.get("ollama"), Mapping) else {}
    legacy = root.get("ollama") if isinstance(root.get("ollama"), Mapping) else {}
    agent = root.get("agent") if isinstance(root.get("agent"), Mapping) else {}
    return OllamaChatClient(
        str(selected.get("url") or legacy.get("url") or "http://localhost:11434/api/chat"),
        str(selected.get("model") or legacy.get("model") or "gemma4:12b"),
        str(selected.get("keep_alive") or legacy.get("keep_alive") or "30m"),
        int(agent.get("context_window", 8192)),
    )
