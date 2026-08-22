"""Thread-safe OpenAI Realtime WebSocket session.

The network transport is deliberately injectable.  Production uses
``websocket-client`` while tests can provide a completely in-memory transport.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlencode

from .protocol import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_VOICE,
    FunctionCall,
    ProtocolError,
    RealtimeServerError,
    append_audio_event,
    clear_audio_event,
    conversation_item_delete_event,
    conversation_text_event,
    conversation_item_truncate_event,
    decode_audio_delta,
    decode_event,
    encode_event,
    function_call_from_event,
    function_call_output_event,
    response_cancel_event,
    response_create_event,
    session_update_event,
)

try:  # Optional at import time so local-only Nexo installations still start.
    import websocket as _websocket_client
except ImportError:  # pragma: no cover - exercised by patching in the tests.
    _websocket_client = None


LOG = logging.getLogger(__name__)


class RealtimeConnectionError(ConnectionError):
    """Sanitized failure from the WebSocket connection lifecycle."""

    def __init__(self, error: BaseException, *, phase: str) -> None:
        self.phase = str(phase)[:24]
        self.cause_type = type(error).__name__[:80]
        self.protocol_failure = isinstance(error, ProtocolError)
        super().__init__("OpenAI Realtime connection cycle failed")


_BENIGN_CONTROL_RACE_CODES = frozenset({
    "response_cancel_not_active",
    "response_already_cancelled",
    "response_already_canceled",
    "conversation_item_already_truncated",
    "item_already_truncated",
})


def _is_benign_control_race(error: Mapping[str, Any]) -> bool:
    """Recognize only known cancel/truncate completion races."""

    code = str(error.get("code") or "").strip().casefold()
    if code in _BENIGN_CONTROL_RACE_CODES:
        return True
    # Inspect free-form text only for classification; never retain or log it.
    message = str(error.get("message") or "").strip().casefold()
    cancel_race = "cancel" in message and any(
        phrase in message
        for phrase in (
            "no active response",
            "no response to cancel",
            "response is not active",
            "response has already been cancelled",
            "response has already been canceled",
            "response has already completed",
        )
    )
    truncate_race = "truncat" in message and "already" in message
    return cancel_race or truncate_race


class RealtimeTransport(Protocol):
    def connect(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout: float,
    ) -> None: ...

    def send(self, payload: str) -> None: ...

    def recv(self) -> str | bytes | Mapping[str, Any] | None: ...

    def close(self) -> None: ...


class SessionState(str, Enum):
    NEW = "new"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


class WebSocketClientTransport:
    """Small adapter around the optional ``websocket-client`` package."""

    def __init__(self) -> None:
        self._socket: Any = None

    def connect(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout: float,
    ) -> None:
        if _websocket_client is None:
            raise RuntimeError(
                "OpenAI Realtime requires websocket-client. "
                "Install it with: pip install -r requirements-openai.txt"
            )
        # Never log or retain this header separately: it contains the API key.
        header_lines = [f"{name}: {value}" for name, value in headers.items()]
        self._socket = _websocket_client.create_connection(
            url,
            header=header_lines,
            timeout=timeout,
        )
        # create_connection's setup timeout would otherwise remain as the
        # steady-state recv timeout. A quiet, healthy Realtime session would
        # be mistaken for a broken connection every ten seconds and exhaust
        # its reconnect budget. close() still unblocks this blocking recv.
        self._socket.settimeout(None)

    def send(self, payload: str) -> None:
        if self._socket is None:
            raise RuntimeError("Realtime WebSocket is not connected")
        self._socket.send(payload)

    def recv(self) -> Any:
        if self._socket is None:
            raise RuntimeError("Realtime WebSocket is not connected")
        return self._socket.recv()

    def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            socket.close()


AudioCallback = Callable[[bytes, Mapping[str, Any]], None]
TranscriptCallback = Callable[[str, bool, Mapping[str, Any]], None]
FunctionCallback = Callable[[FunctionCall], Any]
EventCallback = Callable[[Mapping[str, Any]], None]
ErrorCallback = Callable[[BaseException], None]
StateCallback = Callable[[SessionState], None]


def _default_api_key_loader() -> str:
    """Import the shared secret loader only when a connection is requested."""

    try:
        from nexo_actions.secrets import load_openai_api_key
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "OpenAI credential loader is unavailable; pass api_key_loader=... "
            "or configure nexo_actions.secrets.load_openai_api_key"
        ) from exc
    return load_openai_api_key()


class RealtimeSession:
    """Manage one GA Realtime session with bounded automatic reconnection."""

    _TRANSCRIPT_EVENTS = {
        "conversation.item.input_audio_transcription.delta": ("delta", False),
        "conversation.item.input_audio_transcription.completed": ("transcript", True),
        "response.output_audio_transcript.delta": ("delta", False),
        "response.output_audio_transcript.done": ("transcript", True),
        "response.output_text.delta": ("delta", False),
        "response.output_text.done": ("text", True),
    }

    def __init__(
        self,
        *,
        api_key_loader: Callable[[], str] | None = None,
        transport_factory: Callable[[], RealtimeTransport] | None = None,
        base_url: str = DEFAULT_BASE_URL,
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
        on_audio: AudioCallback | None = None,
        on_transcript: TranscriptCallback | None = None,
        on_function_call: FunctionCallback | None = None,
        on_event: EventCallback | None = None,
        on_error: ErrorCallback | None = None,
        on_interruption: EventCallback | None = None,
        on_state_change: StateCallback | None = None,
        auto_function_results: bool = True,
        auto_cancel_on_speech: bool = True,
        max_reconnects: int = 2,
        reconnect_backoff: float = 0.25,
        connect_timeout: float = 10.0,
    ) -> None:
        if max_reconnects < 0:
            raise ValueError("max_reconnects must not be negative")
        if reconnect_backoff < 0:
            raise ValueError("reconnect_backoff must not be negative")
        if connect_timeout <= 0:
            raise ValueError("connect_timeout must be positive")

        self.api_key_loader = api_key_loader or _default_api_key_loader
        self.transport_factory = transport_factory or WebSocketClientTransport
        self.base_url = base_url.rstrip("?")
        self.model = model
        self.voice = voice
        self.sample_rate = sample_rate
        self.instructions = instructions
        self.tools = tuple(dict(tool) for tool in tools or ())
        self.input_transcription_model = input_transcription_model
        self.input_transcription_prompt = input_transcription_prompt
        self.automatic_response = bool(automatic_response)
        self.automatic_interruption = bool(automatic_interruption)
        self.reasoning_effort = str(reasoning_effort)
        self.max_output_tokens = int(max_output_tokens)
        self.on_audio = on_audio
        self.on_transcript = on_transcript
        self.on_function_call = on_function_call
        self.on_event = on_event
        self.on_error = on_error
        self.on_interruption = on_interruption
        self.on_state_change = on_state_change
        self.auto_function_results = auto_function_results
        self.auto_cancel_on_speech = auto_cancel_on_speech
        self.max_reconnects = max_reconnects
        self.reconnect_backoff = reconnect_backoff
        self.connect_timeout = connect_timeout

        self._state = SessionState.NEW
        self._state_lock = threading.RLock()
        self._connect_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._transport: RealtimeTransport | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._connected_event = threading.Event()
        self._response_active = False
        self._active_response_id: str | None = None
        self._audio_metrics_lock = threading.Lock()
        self._audio_metrics: dict[str, float | int | None] = {}
        self._reset_audio_metrics()

    @property
    def state(self) -> SessionState:
        with self._state_lock:
            return self._state

    @property
    def connected(self) -> bool:
        return self._connected_event.is_set()

    @property
    def response_active(self) -> bool:
        return self._response_active

    @property
    def url(self) -> str:
        separator = "&" if "?" in self.base_url else "?"
        return f"{self.base_url}{separator}{urlencode({'model': self.model})}"

    def start(self) -> threading.Thread:
        """Start the receive/reconnect loop in a daemon thread, idempotently."""

        with self._state_lock:
            if self._state in {SessionState.CLOSING, SessionState.CLOSED}:
                raise RuntimeError("A closed RealtimeSession cannot be restarted")
            if self._thread is not None and self._thread.is_alive():
                return self._thread
            self._stop_event.clear()
            thread = threading.Thread(
                target=self.run_forever,
                name="nexo-openai-realtime",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return thread

    def connect(self) -> None:
        """Open a connection synchronously and send the initial session config."""

        if self.state in {SessionState.CLOSING, SessionState.CLOSED}:
            raise RuntimeError("A closed RealtimeSession cannot be connected")
        if self.connected:
            return
        self._open_transport(reconnecting=False)

    def run_forever(self) -> None:
        """Receive events until closed or the bounded retry budget is exhausted."""

        reconnects = 0
        while not self._stop_event.is_set():
            try:
                if not self.connected:
                    self._open_transport(reconnecting=reconnects > 0)
                while not self._stop_event.is_set():
                    self.receive_once()
                return
            except Exception as exc:
                if self._stop_event.is_set():
                    return
                error = (
                    exc
                    if isinstance(exc, RealtimeConnectionError)
                    else RealtimeConnectionError(exc, phase="receive")
                )
                self._report_error(error)
                self._discard_transport()
                if reconnects >= self.max_reconnects:
                    self._set_state(SessionState.FAILED)
                    return
                reconnects += 1
                self._set_state(SessionState.RECONNECTING)
                delay = self.reconnect_backoff * (2 ** (reconnects - 1))
                if self._stop_event.wait(delay):
                    return

    def receive_once(self) -> dict[str, Any]:
        transport = self._current_transport()
        payload = transport.recv()
        if payload is None or payload == "" or payload == b"":
            raise ConnectionError("Realtime WebSocket closed by the remote peer")
        return self.process_event(payload)

    def process_event(
        self,
        payload: str | bytes | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Process one server event; public to enable deterministic integration tests."""

        event = decode_event(payload)
        event_type = event["type"]

        if event_type == "response.created":
            self._response_active = True
            self._reset_audio_metrics()
            response = event.get("response")
            if isinstance(response, Mapping):
                response_id = response.get("id")
                self._active_response_id = (
                    response_id if isinstance(response_id, str) else None
                )
        elif event_type in {"response.done", "response.cancelled"}:
            self._response_active = False
            self._active_response_id = None
            response = event.get("response")
            status = (
                str(response.get("status") or event_type)
                if isinstance(response, Mapping)
                else event_type
            )
            self._log_audio_metrics(status=status)
        elif event_type == "response.output_audio.delta":
            audio = decode_audio_delta(event)
            received_at = time.monotonic()
            self._record_audio_delta(audio, received_at)
            callback_started = time.monotonic()
            self._call(self.on_audio, audio, event)
            self._record_audio_callback(time.monotonic() - callback_started)
        elif event_type in self._TRANSCRIPT_EVENTS:
            field, is_final = self._TRANSCRIPT_EVENTS[event_type]
            text = event.get(field)
            if isinstance(text, str) and text:
                self._call(self.on_transcript, text, is_final, event)
        elif event_type == "response.function_call_arguments.done":
            self._handle_function_call(event)
        elif event_type == "input_audio_buffer.speech_started":
            self._call(self.on_interruption, event)
            if self.auto_cancel_on_speech and self._response_active:
                self.cancel_response(response_id=self._active_response_id)
        elif event_type == "error":
            error = event.get("error")
            details = error if isinstance(error, Mapping) else {}
            self._report_error(RealtimeServerError(
                error_type=details.get("type"),
                code=details.get("code"),
                param=details.get("param"),
                client_event_id=details.get("event_id"),
                server_event_id=event.get("event_id"),
                benign=_is_benign_control_race(details),
            ))

        self._call(self.on_event, event)
        return event

    def _reset_audio_metrics(self) -> None:
        with self._audio_metrics_lock:
            self._audio_metrics = {
                "chunks": 0,
                "bytes": 0,
                "first_at": None,
                "last_at": None,
                "max_gap_ms": 0.0,
                "gaps_over_120ms": 0,
                "callback_max_ms": 0.0,
                "odd_chunks": 0,
            }

    def _record_audio_delta(self, audio: bytes, received_at: float) -> None:
        with self._audio_metrics_lock:
            previous = self._audio_metrics["last_at"]
            if isinstance(previous, (int, float)):
                gap_ms = max(0.0, (received_at - float(previous)) * 1_000)
                self._audio_metrics["max_gap_ms"] = max(
                    float(self._audio_metrics["max_gap_ms"]),
                    gap_ms,
                )
                if gap_ms > 120.0:
                    self._audio_metrics["gaps_over_120ms"] = (
                        int(self._audio_metrics["gaps_over_120ms"]) + 1
                    )
            else:
                self._audio_metrics["first_at"] = received_at
            self._audio_metrics["last_at"] = received_at
            self._audio_metrics["chunks"] = int(self._audio_metrics["chunks"]) + 1
            self._audio_metrics["bytes"] = int(self._audio_metrics["bytes"]) + len(audio)
            if len(audio) % 2:
                self._audio_metrics["odd_chunks"] = (
                    int(self._audio_metrics["odd_chunks"]) + 1
                )

    def _record_audio_callback(self, elapsed_seconds: float) -> None:
        with self._audio_metrics_lock:
            self._audio_metrics["callback_max_ms"] = max(
                float(self._audio_metrics["callback_max_ms"]),
                max(0.0, elapsed_seconds * 1_000),
            )

    def audio_diagnostics(self) -> dict[str, int | float]:
        """Return aggregate timing metadata without retaining audio content."""

        with self._audio_metrics_lock:
            values = dict(self._audio_metrics)
        first_at = values.get("first_at")
        last_at = values.get("last_at")
        receive_span_ms = (
            max(0.0, (float(last_at) - float(first_at)) * 1_000)
            if isinstance(first_at, (int, float))
            and isinstance(last_at, (int, float))
            else 0.0
        )
        audio_bytes = int(values.get("bytes") or 0)
        return {
            "chunks": int(values.get("chunks") or 0),
            "bytes": audio_bytes,
            "audio_ms": round(
                audio_bytes * 1_000 / max(1, self.sample_rate * 2),
                1,
            ),
            "receive_span_ms": round(receive_span_ms, 1),
            "max_gap_ms": round(float(values.get("max_gap_ms") or 0.0), 1),
            "gaps_over_120ms": int(values.get("gaps_over_120ms") or 0),
            "callback_max_ms": round(
                float(values.get("callback_max_ms") or 0.0),
                1,
            ),
            "odd_chunks": int(values.get("odd_chunks") or 0),
        }

    def _log_audio_metrics(self, *, status: str) -> None:
        metrics = self.audio_diagnostics()
        LOG.info(
            "REALTIME_AUDIO_STREAM status=%s chunks=%d bytes=%d audio_ms=%.1f "
            "receive_span_ms=%.1f max_gap_ms=%.1f gaps_over_120ms=%d "
            "callback_max_ms=%.1f odd_chunks=%d",
            str(status)[:32],
            metrics["chunks"],
            metrics["bytes"],
            metrics["audio_ms"],
            metrics["receive_span_ms"],
            metrics["max_gap_ms"],
            metrics["gaps_over_120ms"],
            metrics["callback_max_ms"],
            metrics["odd_chunks"],
        )

    def send_audio(self, pcm_audio: bytes) -> None:
        self.send_event(append_audio_event(pcm_audio))

    def send_event(self, event: Mapping[str, Any]) -> None:
        payload = encode_event(event)
        with self._send_lock:
            transport = self._current_transport()
            try:
                transport.send(payload)
            except RealtimeConnectionError:
                raise
            except Exception as exc:
                raise RealtimeConnectionError(exc, phase="send") from exc

    def send_function_result(
        self,
        call_id: str,
        output: Any,
        *,
        create_response: bool = True,
    ) -> None:
        self.send_event(function_call_output_event(call_id, output))
        if create_response:
            self.send_event(response_create_event())

    def request_response(self) -> None:
        self.send_event(response_create_event())

    def send_text(self, text: str, *, create_response: bool = True) -> None:
        self.send_event(conversation_text_event(text))
        if create_response:
            self.send_event(response_create_event())

    def delete_item(self, item_id: str) -> None:
        self.send_event(conversation_item_delete_event(item_id))

    def cancel_response(
        self,
        *,
        response_id: str | None = None,
        clear_input: bool = False,
        item_id: str | None = None,
        played_audio_ms: int | None = None,
    ) -> None:
        """Cancel generation and optionally truncate unheard assistant audio."""

        self.send_event(response_cancel_event(response_id))
        if item_id is not None and played_audio_ms is not None:
            self.send_event(
                conversation_item_truncate_event(item_id, played_audio_ms)
            )
        if clear_input:
            self.send_event(clear_audio_event())
        self._response_active = False
        self._active_response_id = None

    def truncate_audio(self, item_id: str, played_audio_ms: int) -> None:
        """Remove unheard audio after generation has already completed."""

        self.send_event(
            conversation_item_truncate_event(item_id, played_audio_ms)
        )

    def wait_until_connected(self, timeout: float | None = None) -> bool:
        return self._connected_event.wait(timeout)

    def close(self) -> None:
        """Stop I/O and close the transport. Safe to call more than once."""

        self._stop_event.set()
        with self._state_lock:
            if self._state == SessionState.CLOSED:
                return
            self._set_state(SessionState.CLOSING)
            thread = self._thread
        self._discard_transport()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(self.connect_timeout, 1.0))
        self._set_state(SessionState.CLOSED)

    def _open_transport(self, *, reconnecting: bool) -> None:
        with self._connect_lock:
            if self._stop_event.is_set():
                raise RuntimeError("RealtimeSession is stopping")
            if self.connected:
                return
            self._set_state(
                SessionState.RECONNECTING if reconnecting else SessionState.CONNECTING
            )
            api_key = self.api_key_loader()
            if not isinstance(api_key, str) or not api_key.strip():
                raise RuntimeError("OPENAI_API_KEY is not configured")

            transport = self.transport_factory()
            try:
                transport.connect(
                    self.url,
                    {"Authorization": f"Bearer {api_key.strip()}"},
                    self.connect_timeout,
                )
                with self._state_lock:
                    if self._stop_event.is_set():
                        raise RuntimeError("RealtimeSession was closed while connecting")
                    self._transport = transport
                    self._connected_event.set()
                self.send_event(
                    session_update_event(
                        model=self.model,
                        voice=self.voice,
                        sample_rate=self.sample_rate,
                        instructions=self.instructions,
                        tools=self.tools,
                        input_transcription_model=self.input_transcription_model,
                        input_transcription_prompt=self.input_transcription_prompt,
                        automatic_response=self.automatic_response,
                        automatic_interruption=self.automatic_interruption,
                        reasoning_effort=self.reasoning_effort,
                        max_output_tokens=self.max_output_tokens,
                    )
                )
                # Announce CONNECTED only after session.update is on the wire,
                # so callbacks cannot race audio ahead of the configuration.
                self._set_state(SessionState.CONNECTED)
            except Exception:
                with self._state_lock:
                    if self._transport is transport:
                        self._transport = None
                    self._connected_event.clear()
                try:
                    transport.close()
                except Exception:
                    pass
                raise

    def _handle_function_call(self, event: Mapping[str, Any]) -> None:
        call = function_call_from_event(event)
        if self.on_function_call is None:
            return
        try:
            output = self.on_function_call(call)
        except Exception as exc:
            self._report_error(exc)
            if self.auto_function_results:
                self.send_function_result(
                    call.call_id,
                    {"ok": False, "error": "The requested tool failed"},
                )
            return
        if self.auto_function_results and output is not None:
            self.send_function_result(call.call_id, output)

    def _current_transport(self) -> RealtimeTransport:
        with self._state_lock:
            if not self._connected_event.is_set() or self._transport is None:
                raise RuntimeError("RealtimeSession is not connected")
            return self._transport

    def _discard_transport(self) -> None:
        with self._state_lock:
            transport, self._transport = self._transport, None
            self._connected_event.clear()
            self._response_active = False
            self._active_response_id = None
        if transport is not None:
            try:
                transport.close()
            except Exception as exc:
                if not self._stop_event.is_set():
                    self._report_error(RealtimeConnectionError(exc, phase="close"))

    def _set_state(self, state: SessionState) -> None:
        with self._state_lock:
            if self._state == state:
                return
            self._state = state
        self._call(self.on_state_change, state)

    def _report_error(self, error: BaseException) -> None:
        if self.on_error is None:
            return
        try:
            self.on_error(error)
        except Exception:
            # A diagnostic callback must never destabilize the audio loop.
            pass

    def _call(self, callback: Callable[..., Any] | None, *args: Any) -> Any:
        if callback is None:
            return None
        try:
            return callback(*args)
        except Exception as exc:
            self._report_error(exc)
            return None

    def __enter__(self) -> "RealtimeSession":
        self.connect()
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()


__all__ = [
    "RealtimeSession",
    "RealtimeConnectionError",
    "RealtimeTransport",
    "SessionState",
    "WebSocketClientTransport",
]
