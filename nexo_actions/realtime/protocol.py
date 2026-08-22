"""Pure helpers for the OpenAI Realtime wire protocol.

This module deliberately contains no networking and no credential handling.  It
is therefore safe to exercise with deterministic unit tests.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


DEFAULT_MODEL = "gpt-realtime-2.1"
DEFAULT_VOICE = "marin"
DEFAULT_SAMPLE_RATE = 24_000
DEFAULT_BASE_URL = "wss://api.openai.com/v1/realtime"


class ProtocolError(ValueError):
    """Raised when a Realtime event cannot be encoded or decoded safely."""


def _diagnostic_token(value: Any, *, limit: int = 96) -> str:
    """Return a bounded log-safe identifier, never free-form server text."""

    if not isinstance(value, str):
        return "-"
    token = "".join(
        character if character.isalnum() or character in "._:-/" else "_"
        for character in value.strip()
    )
    return token[:limit] or "-"


class RealtimeServerError(ProtocolError):
    """Recoverable error event reported by an open Realtime session.

    The server's free-form message is intentionally not retained: it may echo
    client content. Only bounded protocol identifiers are exposed for logs.
    """

    def __init__(
        self,
        *,
        error_type: Any = None,
        code: Any = None,
        param: Any = None,
        client_event_id: Any = None,
        server_event_id: Any = None,
        benign: bool = False,
    ) -> None:
        self.error_type = _diagnostic_token(error_type)
        self.code = _diagnostic_token(code)
        self.param = _diagnostic_token(param)
        self.client_event_id = _diagnostic_token(client_event_id)
        self.server_event_id = _diagnostic_token(server_event_id)
        self.benign = bool(benign)
        super().__init__("OpenAI Realtime reported a recoverable turn error")


@dataclass(frozen=True, slots=True)
class FunctionCall:
    """A completed tool call emitted by a Realtime response."""

    name: str
    call_id: str
    arguments: Any
    raw_arguments: str
    response_id: str | None = None
    item_id: str | None = None


def session_update_event(
    *,
    model: str = DEFAULT_MODEL,
    voice: str = DEFAULT_VOICE,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    instructions: str | None = None,
    tools: Sequence[Mapping[str, Any]] | None = None,
    input_transcription_model: str | None = None,
    input_transcription_prompt: str | None = None,
    automatic_response: bool = True,
    automatic_interruption: bool = True,
    reasoning_effort: str = "minimal",
    max_output_tokens: int = 1024,
) -> dict[str, Any]:
    """Build a GA ``session.update`` event for PCM audio and semantic VAD."""

    if not model.strip():
        raise ProtocolError("Realtime model must not be empty")
    if not voice.strip():
        raise ProtocolError("Realtime voice must not be empty")
    if sample_rate <= 0:
        raise ProtocolError("Audio sample rate must be positive")
    reasoning_effort = str(reasoning_effort).strip().casefold()
    if reasoning_effort not in {"minimal", "low", "medium", "high"}:
        raise ProtocolError("Realtime reasoning effort is invalid")
    if not isinstance(max_output_tokens, int) or not 64 <= max_output_tokens <= 4096:
        raise ProtocolError("Realtime max output tokens must be between 64 and 4096")

    turn_detection: dict[str, Any] = {"type": "semantic_vad"}
    if not automatic_response:
        turn_detection["create_response"] = False
    if not automatic_interruption:
        turn_detection["interrupt_response"] = False
    audio_input: dict[str, Any] = {
        "format": {"type": "audio/pcm", "rate": sample_rate},
        "turn_detection": turn_detection,
    }
    if input_transcription_model:
        audio_input["transcription"] = {
            "model": str(input_transcription_model),
            "language": "pt",
        }
        if input_transcription_prompt:
            audio_input["transcription"]["prompt"] = str(
                input_transcription_prompt
            )[:1_000]

    session: dict[str, Any] = {
        "type": "realtime",
        "model": model,
        "output_modalities": ["audio"],
        "reasoning": {"effort": reasoning_effort},
        "max_output_tokens": max_output_tokens,
        "audio": {
            "input": audio_input,
            "output": {
                "format": {"type": "audio/pcm", "rate": sample_rate},
                "voice": voice,
            },
        },
    }
    if instructions:
        session["instructions"] = instructions
    if tools:
        session["tools"] = [dict(tool) for tool in tools]

    return {"type": "session.update", "session": session}


def append_audio_event(pcm_audio: bytes) -> dict[str, str]:
    """Encode one raw PCM chunk for ``input_audio_buffer.append``."""

    if not isinstance(pcm_audio, bytes):
        raise ProtocolError("PCM audio must be bytes")
    if not pcm_audio:
        raise ProtocolError("PCM audio chunk must not be empty")
    return {
        "type": "input_audio_buffer.append",
        "audio": base64.b64encode(pcm_audio).decode("ascii"),
    }


def clear_audio_event() -> dict[str, str]:
    return {"type": "input_audio_buffer.clear"}


def response_cancel_event(response_id: str | None = None) -> dict[str, str]:
    event = {"type": "response.cancel"}
    if response_id:
        event["response_id"] = response_id
    return event


def conversation_item_truncate_event(
    item_id: str,
    audio_end_ms: int,
    *,
    content_index: int = 0,
) -> dict[str, Any]:
    """Remove assistant audio that was generated but never played to the user."""

    if not item_id:
        raise ProtocolError("Conversation item id must not be empty")
    if audio_end_ms < 0:
        raise ProtocolError("audio_end_ms must not be negative")
    return {
        "type": "conversation.item.truncate",
        "item_id": item_id,
        "content_index": content_index,
        "audio_end_ms": audio_end_ms,
    }


def function_call_output_event(call_id: str, output: Any) -> dict[str, Any]:
    if not call_id:
        raise ProtocolError("Function call id must not be empty")
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": serialize_function_output(output),
        },
    }


def response_create_event() -> dict[str, str]:
    return {"type": "response.create"}


def conversation_text_event(text: str) -> dict[str, Any]:
    value = " ".join(str(text).split()).strip()
    if not value:
        raise ProtocolError("Conversation text must not be empty")
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": value}],
        },
    }


def conversation_item_delete_event(item_id: str) -> dict[str, str]:
    if not item_id:
        raise ProtocolError("Conversation item id must not be empty")
    return {"type": "conversation.item.delete", "item_id": item_id}


def serialize_function_output(output: Any) -> str:
    """Return the string payload accepted by a function-call output item."""

    if isinstance(output, str):
        return output
    try:
        return json.dumps(
            output,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolError("Function output is not JSON serializable") from exc


def decode_audio_delta(event: Mapping[str, Any]) -> bytes:
    encoded = event.get("delta")
    if not isinstance(encoded, str) or not encoded:
        raise ProtocolError("Audio delta is missing its base64 payload")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ProtocolError("Audio delta contains invalid base64") from exc


def function_call_from_event(event: Mapping[str, Any]) -> FunctionCall:
    if event.get("type") != "response.function_call_arguments.done":
        raise ProtocolError("Event is not a completed function call")

    name = event.get("name")
    call_id = event.get("call_id")
    raw_arguments = event.get("arguments")
    if not isinstance(name, str) or not name:
        raise ProtocolError("Function call is missing its name")
    if not isinstance(call_id, str) or not call_id:
        raise ProtocolError("Function call is missing its call_id")
    if not isinstance(raw_arguments, str):
        raise ProtocolError("Function call arguments must be a JSON string")

    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ProtocolError("Function call arguments contain invalid JSON") from exc

    return FunctionCall(
        name=name,
        call_id=call_id,
        arguments=arguments,
        raw_arguments=raw_arguments,
        response_id=_optional_string(event.get("response_id")),
        item_id=_optional_string(event.get("item_id")),
    )


def encode_event(event: Mapping[str, Any]) -> str:
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise ProtocolError("Realtime event is missing its type")
    try:
        return json.dumps(
            dict(event),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolError("Realtime event is not JSON serializable") from exc


def decode_event(payload: str | bytes | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        event = dict(payload)
    else:
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProtocolError("Realtime event is not UTF-8") from exc
        if not isinstance(payload, str):
            raise ProtocolError("Realtime event must be JSON text or a mapping")
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProtocolError("Realtime event contains invalid JSON") from exc

    if not isinstance(event, dict):
        raise ProtocolError("Realtime event must be a JSON object")
    if not isinstance(event.get("type"), str) or not event["type"]:
        raise ProtocolError("Realtime event is missing its type")
    return event


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
