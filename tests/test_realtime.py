import base64
import io
import json
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock, patch

from nexo_actions.realtime import (
    DEFAULT_MODEL,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_VOICE,
    PulseCapture,
    PulsePlayback,
    RealtimeSession,
    RealtimeServerError,
    SessionState,
    WebSocketClientTransport,
)
from nexo_actions.realtime.protocol import (
    ProtocolError,
    append_audio_event,
    conversation_text_event,
    decode_event,
    session_update_event,
)
import nexo_actions.realtime.session as session_module


class FakeTransport:
    def __init__(self, incoming=()):
        self.incoming = list(incoming)
        self.connected = []
        self.sent = []
        self.close_count = 0

    def connect(self, url, headers, timeout):
        self.connected.append({"url": url, "headers": dict(headers), "timeout": timeout})

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        if not self.incoming:
            return None
        value = self.incoming.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self):
        self.close_count += 1


class RealtimeProtocolTests(unittest.TestCase):
    def test_session_update_uses_ga_pcm24k_semantic_vad_and_marin(self):
        tools = [{
            "type": "function",
            "name": "set_volume",
            "description": "Set volume",
            "parameters": {"type": "object", "properties": {}},
        }]

        event = session_update_event(
            instructions="Você é o Nexo.",
            tools=tools,
            input_transcription_model="gpt-transcribe",
            input_transcription_prompt="Nexo, Codex e Wave Chat.",
        )

        session = event["session"]
        self.assertEqual(event["type"], "session.update")
        self.assertEqual(session["type"], "realtime")
        self.assertEqual(session["model"], DEFAULT_MODEL)
        self.assertEqual(session["output_modalities"], ["audio"])
        self.assertEqual(session["reasoning"], {"effort": "minimal"})
        self.assertEqual(session["max_output_tokens"], 1024)
        self.assertEqual(
            session["audio"]["input"]["format"],
            {"type": "audio/pcm", "rate": 24_000},
        )
        self.assertEqual(
            session["audio"]["input"]["turn_detection"],
            {"type": "semantic_vad"},
        )
        self.assertEqual(session["audio"]["output"]["voice"], "marin")
        self.assertEqual(
            session["audio"]["input"]["transcription"],
            {
                "model": "gpt-transcribe",
                "language": "pt",
                "prompt": "Nexo, Codex e Wave Chat.",
            },
        )
        self.assertEqual(
            session["audio"]["output"]["format"],
            {"type": "audio/pcm", "rate": 24_000},
        )
        self.assertEqual(session["tools"], tools)

    def test_session_update_validates_realtime_reasoning_and_concise_limit(self):
        event = session_update_event(
            reasoning_effort="low",
            max_output_tokens=512,
        )
        self.assertEqual(event["session"]["reasoning"]["effort"], "low")
        self.assertEqual(event["session"]["max_output_tokens"], 512)
        with self.assertRaises(ProtocolError):
            session_update_event(reasoning_effort="none")
        with self.assertRaises(ProtocolError):
            session_update_event(max_output_tokens=32)

    def test_audio_append_is_base64_and_rejects_empty_chunks(self):
        pcm = b"\x00\x01\xfe\xff"
        event = append_audio_event(pcm)

        self.assertEqual(event["type"], "input_audio_buffer.append")
        self.assertEqual(base64.b64decode(event["audio"]), pcm)
        with self.assertRaises(ProtocolError):
            append_audio_event(b"")

    def test_text_item_uses_ga_conversation_shape(self):
        event = conversation_text_event("  Olá   Nexo  ")
        self.assertEqual(event["type"], "conversation.item.create")
        self.assertEqual(event["item"]["role"], "user")
        self.assertEqual(
            event["item"]["content"],
            [{"type": "input_text", "text": "Olá Nexo"}],
        )
        with self.assertRaises(ProtocolError):
            conversation_text_event("   ")

    def test_decode_event_rejects_non_object_json(self):
        with self.assertRaises(ProtocolError):
            decode_event("[]")


class RealtimeSessionTests(unittest.TestCase):
    def session(self, transport=None, **kwargs):
        transport = transport or FakeTransport()
        session = RealtimeSession(
            api_key_loader=lambda: "test-key-never-log",
            transport_factory=lambda: transport,
            reconnect_backoff=0,
            **kwargs,
        )
        self.addCleanup(session.close)
        return session, transport

    def test_connect_uses_ga_url_and_sends_session_update(self):
        session, transport = self.session(
            instructions="Seja natural.",
            reasoning_effort="minimal",
            max_output_tokens=640,
        )

        session.connect()

        connection = transport.connected[0]
        self.assertEqual(
            connection["url"],
            "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1",
        )
        self.assertEqual(connection["headers"]["Authorization"], "Bearer test-key-never-log")
        self.assertNotIn("OpenAI-Beta", connection["headers"])
        self.assertNotIn("test-key-never-log", connection["url"])
        self.assertEqual(transport.sent[0]["type"], "session.update")
        self.assertEqual(
            transport.sent[0]["session"]["reasoning"],
            {"effort": "minimal"},
        )
        self.assertEqual(
            transport.sent[0]["session"]["max_output_tokens"],
            640,
        )
        self.assertEqual(session.state, SessionState.CONNECTED)

    def test_streams_audio_and_decodes_output_delta(self):
        received = []
        session, transport = self.session(
            on_audio=lambda audio, event: received.append((audio, event["type"])),
        )
        session.connect()

        session.send_audio(b"input-pcm")
        session.process_event({
            "type": "response.output_audio.delta",
            "delta": base64.b64encode(b"output-pcm").decode("ascii"),
        })

        self.assertEqual(
            base64.b64decode(transport.sent[-1]["audio"]),
            b"input-pcm",
        )
        self.assertEqual(received, [(b"output-pcm", "response.output_audio.delta")])

    def test_audio_stream_diagnostics_are_aggregate_metadata_only(self):
        session, _transport = self.session()
        session.connect()
        first = bytes(480)
        second = bytes(960)

        session.process_event({
            "type": "response.created",
            "response": {"id": "response-audio"},
        })
        session.process_event({
            "type": "response.output_audio.delta",
            "delta": base64.b64encode(first).decode("ascii"),
        })
        session.process_event({
            "type": "response.output_audio.delta",
            "delta": base64.b64encode(second).decode("ascii"),
        })
        with self.assertLogs(
            "nexo_actions.realtime.session",
            level="INFO",
        ) as captured:
            session.process_event({
                "type": "response.done",
                "response": {"id": "response-audio", "status": "completed"},
            })

        metrics = session.audio_diagnostics()
        self.assertEqual(metrics["chunks"], 2)
        self.assertEqual(metrics["bytes"], len(first) + len(second))
        self.assertEqual(metrics["audio_ms"], 30.0)
        self.assertEqual(metrics["odd_chunks"], 0)
        log_output = " ".join(captured.output)
        self.assertIn("REALTIME_AUDIO_STREAM", log_output)
        self.assertNotIn(base64.b64encode(first).decode("ascii"), log_output)

    def test_dispatches_input_and_output_transcripts(self):
        transcripts = []
        session, _transport = self.session(
            on_transcript=lambda text, final, event: transcripts.append(
                (text, final, event["type"])
            ),
        )
        session.connect()

        session.process_event({
            "type": "conversation.item.input_audio_transcription.delta",
            "delta": "abra ",
        })
        session.process_event({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "abra o Chrome",
        })
        session.process_event({
            "type": "response.output_audio_transcript.delta",
            "delta": "Abrindo.",
        })
        session.process_event({
            "type": "response.output_audio_transcript.done",
            "transcript": "Abrindo.",
        })

        self.assertEqual(transcripts, [
            ("abra ", False, "conversation.item.input_audio_transcription.delta"),
            ("abra o Chrome", True, "conversation.item.input_audio_transcription.completed"),
            ("Abrindo.", False, "response.output_audio_transcript.delta"),
            ("Abrindo.", True, "response.output_audio_transcript.done"),
        ])

    def test_function_callback_returns_output_and_requests_continuation(self):
        calls = []
        session, transport = self.session(
            on_function_call=lambda call: calls.append(call) or {
                "ok": True,
                "volume": call.arguments["level"],
            },
        )
        session.connect()

        session.process_event({
            "type": "response.function_call_arguments.done",
            "name": "set_volume",
            "call_id": "call-123",
            "arguments": '{"level":42}',
            "item_id": "item-1",
        })

        self.assertEqual(calls[0].name, "set_volume")
        self.assertEqual(calls[0].arguments, {"level": 42})
        output = transport.sent[-2]
        self.assertEqual(output["type"], "conversation.item.create")
        self.assertEqual(output["item"]["call_id"], "call-123")
        self.assertEqual(
            json.loads(output["item"]["output"]),
            {"ok": True, "volume": 42},
        )
        self.assertEqual(transport.sent[-1], {"type": "response.create"})

    def test_speech_started_interrupts_only_an_active_response(self):
        interruptions = []
        session, transport = self.session(
            on_interruption=lambda event: interruptions.append(event["audio_start_ms"]),
        )
        session.connect()
        sent_before = len(transport.sent)

        session.process_event({
            "type": "input_audio_buffer.speech_started",
            "audio_start_ms": 100,
        })
        self.assertEqual(len(transport.sent), sent_before)

        session.process_event({
            "type": "response.created",
            "response": {"id": "response-1"},
        })
        session.process_event({
            "type": "input_audio_buffer.speech_started",
            "audio_start_ms": 250,
        })

        self.assertEqual(interruptions, [100, 250])
        self.assertEqual(transport.sent[-1], {
            "type": "response.cancel",
            "response_id": "response-1",
        })

    def test_explicit_cancel_can_truncate_and_clear(self):
        session, transport = self.session()
        session.connect()

        session.cancel_response(
            item_id="assistant-item",
            played_audio_ms=375,
            clear_input=True,
        )

        self.assertEqual([event["type"] for event in transport.sent[-3:]], [
            "response.cancel",
            "conversation.item.truncate",
            "input_audio_buffer.clear",
        ])
        self.assertEqual(transport.sent[-2]["audio_end_ms"], 375)

    def test_server_error_is_sanitized_recoverable_and_keeps_socket_open(self):
        reported = []
        session, transport = self.session(on_error=reported.append)
        session.connect()

        session.process_event({
            "type": "error",
            "event_id": "event-server-1",
            "error": {
                "type": "invalid_request_error",
                "code": "bad_turn_shape",
                "param": "response.tools[0]",
                "event_id": "event-client-1",
                "message": "secret spoken content must not reach diagnostics",
            },
        })

        self.assertEqual(session.state, SessionState.CONNECTED)
        self.assertTrue(session.connected)
        self.assertEqual(transport.close_count, 0)
        self.assertEqual(len(reported), 1)
        error = reported[0]
        self.assertIsInstance(error, RealtimeServerError)
        self.assertEqual(error.code, "bad_turn_shape")
        self.assertEqual(error.param, "response.tools_0_")
        self.assertFalse(error.benign)
        self.assertNotIn("secret spoken content", str(error))

    def test_only_known_cancel_or_truncate_races_are_benign(self):
        reported = []
        session, _transport = self.session(on_error=reported.append)
        session.connect()

        session.process_event({
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_request_error",
                "message": "Cancellation failed: no active response found",
            },
        })
        session.process_event({
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_value",
                "message": "conversation.item.truncate audio_end_ms is invalid",
            },
        })

        self.assertTrue(reported[0].benign)
        self.assertFalse(reported[1].benign)

    def test_close_is_idempotent(self):
        session, transport = self.session()
        session.connect()

        session.close()
        session.close()

        self.assertEqual(transport.close_count, 1)
        self.assertEqual(session.state, SessionState.CLOSED)
        with self.assertRaises(RuntimeError):
            session.send_audio(b"late")

    def test_reconnection_budget_is_bounded(self):
        transports = [
            FakeTransport([ConnectionError("first")]),
            FakeTransport([ConnectionError("second")]),
        ]
        created = []

        def factory():
            transport = transports[len(created)]
            created.append(transport)
            return transport

        errors = []
        session = RealtimeSession(
            api_key_loader=lambda: "test-key",
            transport_factory=factory,
            max_reconnects=1,
            reconnect_backoff=0,
            on_error=errors.append,
        )
        self.addCleanup(session.close)

        session.run_forever()

        self.assertEqual(len(created), 2)
        self.assertEqual(session.state, SessionState.FAILED)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(t.close_count == 1 for t in created))

    def test_optional_websocket_dependency_has_actionable_error(self):
        with patch.object(session_module, "_websocket_client", None):
            with self.assertRaisesRegex(RuntimeError, "requirements-openai.txt"):
                WebSocketClientTransport().connect(
                    "wss://example.invalid",
                    {"Authorization": "Bearer dummy"},
                    1,
                )

    def test_websocket_connect_timeout_does_not_become_idle_read_timeout(self):
        socket = Mock()
        websocket_module = Mock()
        websocket_module.create_connection.return_value = socket
        transport = WebSocketClientTransport()

        with patch.object(session_module, "_websocket_client", websocket_module):
            transport.connect(
                "wss://example.invalid",
                {"Authorization": "Bearer dummy"},
                10,
            )

        websocket_module.create_connection.assert_called_once()
        socket.settimeout.assert_called_once_with(None)


class BlockingReader:
    def __init__(self):
        self.released = threading.Event()
        self.closed = False

    def read(self, _size):
        self.released.wait(1)
        return b""

    def close(self):
        self.closed = True
        self.released.set()


class FakeProcess:
    def __init__(self, *, capture=False):
        self.alive = True
        self.terminated = 0
        self.killed = 0
        self.stdout = BlockingReader() if capture else None
        self.stdin = None if capture else io.BytesIO()
        self.stderr = io.BytesIO()

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated += 1
        self.alive = False
        if self.stdout is not None:
            self.stdout.released.set()

    def kill(self):
        self.killed += 1
        self.alive = False
        if self.stdout is not None:
            self.stdout.released.set()

    def wait(self, timeout=None):
        if self.alive:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0


class BlockingWriter:
    def __init__(self):
        self.entered = threading.Event()
        self.released = threading.Event()
        self.data = bytearray()

    def write(self, value):
        self.entered.set()
        self.released.wait(1)
        self.data.extend(value)
        return len(value)

    def close(self):
        self.released.set()


class PulseAudioTests(unittest.TestCase):
    @staticmethod
    def _wait_for(predicate, timeout=0.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.005)
        return bool(predicate())

    def test_capture_uses_aec_source_without_shell_and_is_cancelable(self):
        calls = []
        process = FakeProcess(capture=True)

        def popen(command, **kwargs):
            calls.append((command, kwargs))
            return process

        capture = PulseCapture(popen_factory=popen, stop_timeout=0.01)
        capture.start(lambda _chunk: None)
        capture.stop()

        command, kwargs = calls[0]
        self.assertEqual(command[0], "parec")
        self.assertIn("--device=NexoAECSource", command)
        self.assertIn("--rate=24000", command)
        self.assertFalse(kwargs["shell"])
        self.assertEqual(
            kwargs["env"]["PULSE_SERVER"],
            "unix:/tmp/nexo-pulse/native",
        )
        self.assertEqual(process.terminated, 1)
        self.assertFalse(capture.running)

    def test_capture_reports_immediate_wrong_server_failure(self):
        reported = []
        process = FakeProcess(capture=True)
        process.alive = False
        process.stderr = io.BytesIO(b"No such entity")

        capture = PulseCapture(
            popen_factory=lambda _command, **_kwargs: process,
            stop_timeout=0.01,
            on_error=reported.append,
        )

        with self.assertRaisesRegex(RuntimeError, "No such entity"):
            capture.start(lambda _chunk: None)
        self.assertEqual(len(reported), 1)
        self.assertIn("No such entity", str(capture.last_error))

    def test_playback_uses_aec_sink_and_interrupt_restarts_pacat(self):
        calls = []

        def popen(command, **kwargs):
            process = FakeProcess(capture=False)
            calls.append((command, kwargs, process))
            return process

        playback = PulsePlayback(
            popen_factory=popen,
            stop_timeout=0.01,
            prebuffer_msec=0,
        )
        playback.write(b"first")
        self.assertTrue(self._wait_for(lambda: len(calls) == 1))
        first_process = calls[0][2]
        self.assertTrue(
            self._wait_for(lambda: first_process.stdin.getvalue() == b"first")
        )

        playback.interrupt()
        playback.write(b"second")
        self.assertTrue(self._wait_for(lambda: len(calls) == 2))
        second_process = calls[1][2]
        self.assertTrue(
            self._wait_for(lambda: second_process.stdin.getvalue() == b"second")
        )
        playback.close()
        playback.close()

        self.assertEqual(calls[0][0][0], "pacat")
        self.assertIn("--device=NexoAECSink", calls[0][0])
        self.assertIn("--rate=24000", calls[0][0])
        self.assertIn("--latency-msec=180", calls[0][0])
        self.assertIn("--process-time-msec=20", calls[0][0])
        self.assertFalse(calls[0][1]["shell"])
        self.assertEqual(
            calls[0][1]["env"]["PULSE_SERVER"],
            "unix:/tmp/nexo-pulse/native",
        )
        self.assertEqual(first_process.terminated, 1)
        self.assertEqual(second_process.terminated, 1)
        with self.assertRaises(RuntimeError):
            playback.write(b"late")

    def test_playback_prebuffers_bursty_deltas_before_starting_pacat(self):
        calls = []

        def popen(command, **kwargs):
            process = FakeProcess(capture=False)
            calls.append((command, kwargs, process))
            return process

        playback = PulsePlayback(
            sample_rate=1_000,
            channels=1,
            prebuffer_msec=100,
            process_time_msec=20,
            popen_factory=popen,
            stop_timeout=0.01,
        )
        playback.write(b"a" * 100)
        time.sleep(0.01)
        self.assertEqual(calls, [])

        playback.write(b"b" * 100)
        self.assertTrue(self._wait_for(lambda: len(calls) == 1))
        process = calls[0][2]
        self.assertTrue(
            self._wait_for(
                lambda: process.stdin.getvalue() == b"a" * 100 + b"b" * 100
            )
        )
        playback.close()

    def test_playback_reports_early_pacat_exit_without_retry_loop(self):
        calls = []
        reported = []

        def popen(command, **kwargs):
            process = FakeProcess(capture=False)
            process.alive = False
            process.stderr = io.BytesIO(b"No such entity")
            calls.append(process)
            return process

        playback = PulsePlayback(
            prebuffer_msec=0,
            popen_factory=popen,
            stop_timeout=0.01,
            on_error=reported.append,
        )
        playback.write(b"audio")
        self.assertTrue(self._wait_for(lambda: playback.last_error is not None))
        playback.close()

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(reported), 1)
        self.assertIn("No such entity", str(playback.last_error))

    def test_playback_pipe_backpressure_never_blocks_realtime_writer(self):
        process = FakeProcess(capture=False)
        writer = BlockingWriter()
        process.stdin = writer
        playback = PulsePlayback(
            prebuffer_msec=0,
            popen_factory=lambda _command, **_kwargs: process,
            stop_timeout=0.01,
        )

        started = time.monotonic()
        playback.write(b"a" * 1_000)
        self.assertLess(time.monotonic() - started, 0.05)
        self.assertTrue(writer.entered.wait(0.5))

        started = time.monotonic()
        playback.write(b"b" * 1_000)
        self.assertLess(time.monotonic() - started, 0.05)
        # While the child is blocked, unheard PCM remains visible in the tail
        # estimate instead of aging from its network enqueue timestamp.
        self.assertGreater(playback.remaining_seconds, 0.03)

        writer.released.set()
        self.assertTrue(
            self._wait_for(lambda: len(writer.data) == 2_000)
        )
        self.assertGreater(playback.remaining_seconds, 0.15)
        self.assertTrue(playback.wait_until_idle(timeout=1.0))
        playback.close()

    def test_playback_busy_tracks_predicted_pulse_tail_and_interrupt_clears_it(self):
        process = FakeProcess(capture=False)
        playback = PulsePlayback(
            sample_rate=1_000,
            channels=1,
            prebuffer_msec=0,
            latency_msec=20,
            process_time_msec=10,
            rebuffer_idle_msec=20,
            popen_factory=lambda _command, **_kwargs: process,
            stop_timeout=0.01,
        )

        playback.write(bytes(40))  # 20 ms of PCM plus a 20 ms Pulse tail.
        self.assertTrue(playback.busy)
        self.assertGreater(playback.remaining_seconds, 0)
        self.assertTrue(playback.wait_until_idle(timeout=0.5))
        self.assertFalse(playback.busy)

        playback.write(bytes(400))
        self.assertTrue(playback.busy)
        playback.interrupt()
        self.assertFalse(playback.busy)
        self.assertEqual(playback.remaining_seconds, 0)
        playback.close()

    def test_playback_rebuffers_after_tail_drains_without_restarting_pacat(self):
        calls = []

        def popen(command, **kwargs):
            process = FakeProcess(capture=False)
            calls.append((command, kwargs, process))
            return process

        playback = PulsePlayback(
            sample_rate=1_000,
            channels=1,
            prebuffer_msec=40,
            latency_msec=20,
            process_time_msec=20,
            rebuffer_idle_msec=180,
            popen_factory=popen,
            stop_timeout=0.01,
        )
        playback.begin_response()
        first = b"a" * 80
        playback.write(first)
        self.assertTrue(self._wait_for(lambda: len(calls) == 1))
        process = calls[0][2]
        self.assertTrue(
            self._wait_for(lambda: process.stdin.getvalue() == first)
        )

        # Let the conservative Pulse tail drain, but stay below the explicit
        # rebuffer-idle threshold. Tail detection itself must re-arm buffering.
        self.assertTrue(playback.wait_until_idle(timeout=0.5))
        playback.write(b"b" * 40)
        time.sleep(0.01)
        self.assertEqual(process.stdin.getvalue(), first)
        playback.write(b"c" * 40)
        self.assertTrue(
            self._wait_for(
                lambda: process.stdin.getvalue() == first + b"b" * 40 + b"c" * 40
            )
        )

        metrics = playback.diagnostics()
        self.assertEqual(len(calls), 1)
        self.assertEqual(metrics["process_starts"], 1)
        self.assertEqual(metrics["enqueued_bytes"], 160)
        self.assertEqual(metrics["delivered_bytes"], 160)
        self.assertGreaterEqual(metrics["rebuffer_events"], 1)
        playback.close()


if __name__ == "__main__":
    unittest.main()
