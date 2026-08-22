"""Local Ollama adapter retained as an explicitly selectable fallback."""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Mapping, Sequence


class OllamaChatClient:
    provider_name = "ollama"
    supports_semantic_tools = False

    def __init__(
        self,
        url: str,
        model: str,
        keep_alive: str = "30m",
        context_window: int = 8192,
    ) -> None:
        self.url = str(url)
        self.model = str(model)
        self.keep_alive = str(keep_alive)
        self.context_window = max(4096, min(int(context_window), 16384))

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        *,
        timeout: float,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_ctx": self.context_window,
            },
            "keep_alive": self.keep_alive,
        }
        if tools:
            payload["tools"] = list(tools)
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=max(1.0, timeout)) as response:
            return json.loads(response.read().decode("utf-8"))
