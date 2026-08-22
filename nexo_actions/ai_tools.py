"""Observability for the local Ollama/Nexo AI runtime."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from .config import load_config
from .system_tools import _gpu_status


def _ollama_base_url(chat_url: str) -> str:
    parsed = urlsplit(chat_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def ai_runtime_status() -> dict:
    config = load_config().get("ollama", {})
    chat_url = str(config.get("url", "http://localhost:11434/api/chat"))
    request = urllib.request.Request(_ollama_base_url(chat_url) + "/api/ps")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "tool": "ai_runtime_status",
            "ollama_responding": False,
            "error_type": type(exc).__name__,
        }
    models = []
    for model in payload.get("models", []):
        details = model.get("details") or {}
        models.append({
            "name": model.get("name") or model.get("model"),
            "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"),
            "size_bytes": model.get("size"),
            "vram_bytes": model.get("size_vram"),
            "context_length": model.get("context_length"),
            "expires_at": model.get("expires_at"),
        })
    return {
        "ok": True,
        "tool": "ai_runtime_status",
        "ollama_responding": True,
        "configured_model": config.get("model", "gemma4:12b"),
        "loaded_models": models,
        "gpu": _gpu_status(),
    }


AI_RUNTIME_TOOL = {
    "type": "function",
    "function": {
        "name": "ai_runtime_status",
        "description": (
            "Consulta o próprio runtime do Nexo: Ollama, modelos carregados, "
            "contexto e memória de GPU usada pelo modelo."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}
