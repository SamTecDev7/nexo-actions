"""Small provider contract shared by the local and OpenAI runtimes."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


class ProviderError(RuntimeError):
    """Base error safe to surface without leaking credentials or payloads."""


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


@runtime_checkable
class ChatProvider(Protocol):
    provider_name: str
    supports_semantic_tools: bool

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        *,
        timeout: float,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Return an Ollama-compatible ``{"message": ...}`` envelope."""
