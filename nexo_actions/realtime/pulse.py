"""Cancelable PulseAudio PCM capture and jitter-buffered AEC playback.

The Nexo AEC devices live on an isolated PulseAudio server. WSLg also exports
its own Pulse server, so relying on the process environment silently connects
parec/pacat to the wrong server when OVOS is started by systemd.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Any, BinaryIO, Callable, Protocol

from .protocol import DEFAULT_SAMPLE_RATE


AEC_SOURCE = "NexoAECSource"
AEC_SINK = "NexoAECSink"
PULSE_SERVER = "unix:/tmp/nexo-pulse/native"
LOG = logging.getLogger(__name__)


class ProcessFactory(Protocol):
    def __call__(self, command: list[str], **kwargs: Any) -> Any: ...


def capture_command(
    *,
    source: str = AEC_SOURCE,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = 1,
) -> list[str]:
    return [
        "parec",
        f"--device={source}",
        "--raw",
        f"--rate={sample_rate}",
        f"--channels={channels}",
        "--format=s16le",
    ]


def playback_command(
    *,
    sink: str = AEC_SINK,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = 1,
    latency_msec: int = 180,
    process_time_msec: int = 20,
) -> list[str]:
    return [
        "pacat",
        "--playback",
        f"--device={sink}",
        "--raw",
        f"--rate={sample_rate}",
        f"--channels={channels}",
        "--format=s16le",
        f"--latency-msec={latency_msec}",
        f"--process-time-msec={process_time_msec}",
        "--client-name=nexo-realtime",
        "--stream-name=Nexo Realtime Voice",
    ]


def _pulse_environment(server: str) -> dict[str, str]:
    """Return a private child environment without mutating OVOS globally."""

    environment = os.environ.copy()
    environment["PULSE_SERVER"] = server
    return environment


def _process_failure(process: Any, executable: str) -> RuntimeError:
    return_code = process.poll()
    detail = ""
    stderr = getattr(process, "stderr", None)
    if stderr is not None and return_code is not None:
        try:
            raw = stderr.read()
            if isinstance(raw, bytes):
                detail = raw.decode("utf-8", errors="replace")
            elif raw is not None:
                detail = str(raw)
        except Exception:
            pass
    detail = " ".join(detail.split())[:240]
    suffix = f": {detail}" if detail else ""
    return RuntimeError(f"{executable} exited with status {return_code}{suffix}")


class PulseCapture:
    """Read PCM chunks from ``NexoAECSource`` on a background thread."""

    def __init__(
        self,
        *,
        source: str = AEC_SOURCE,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = 1,
        chunk_frames: int = 2_400,
        pulse_server: str = PULSE_SERVER,
        popen_factory: ProcessFactory = subprocess.Popen,
        stop_timeout: float = 1.0,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        if sample_rate <= 0 or channels <= 0 or chunk_frames <= 0:
            raise ValueError("Pulse capture audio dimensions must be positive")
        self.source = source
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_frames = chunk_frames
        self.pulse_server = pulse_server
        self.popen_factory = popen_factory
        self.stop_timeout = stop_timeout
        self.on_error = on_error
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._process: Any = None
        self._thread: threading.Thread | None = None
        self._last_error: BaseException | None = None

    @property
    def command(self) -> list[str]:
        return capture_command(
            source=self.source,
            sample_rate=self.sample_rate,
            channels=self.channels,
        )

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def last_error(self) -> BaseException | None:
        with self._lock:
            return self._last_error

    def start(self, on_chunk: Callable[[bytes], None]) -> threading.Thread:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._thread
            self._cancel.clear()
            process = self.popen_factory(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                shell=False,
                env=_pulse_environment(self.pulse_server),
            )
            if process.stdout is None:
                self._terminate(process)
                raise RuntimeError("parec did not expose an audio stream")
            if process.poll() is not None:
                error = _process_failure(process, "parec")
                self._report_error(error)
                self._terminate(process)
                raise error
            self._process = process
            thread = threading.Thread(
                target=self._read_loop,
                args=(process, process.stdout, on_chunk),
                name="nexo-pulse-capture",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return thread

    def stop(self) -> None:
        self._cancel.set()
        with self._lock:
            process = self._process
            thread = self._thread
        if process is not None:
            self._terminate(process)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(self.stop_timeout, 0.1))
        with self._lock:
            if self._process is process:
                self._process = None
            if self._thread is thread:
                self._thread = None

    close = stop

    def _read_loop(
        self,
        process: Any,
        stream: BinaryIO,
        on_chunk: Callable[[bytes], None],
    ) -> None:
        chunk_bytes = self.chunk_frames * self.channels * 2
        try:
            while not self._cancel.is_set():
                chunk = stream.read(chunk_bytes)
                if not chunk:
                    if not self._cancel.is_set():
                        try:
                            process.wait(timeout=self.stop_timeout)
                        except subprocess.TimeoutExpired:
                            pass
                        self._report_error(_process_failure(process, "parec"))
                    break
                on_chunk(bytes(chunk))
        except Exception as exc:
            if not self._cancel.is_set():
                self._report_error(exc)
        finally:
            try:
                stream.close()
            except Exception:
                pass
            with self._lock:
                if self._process is process:
                    self._process = None
                if self._thread is threading.current_thread():
                    self._thread = None

    def _report_error(self, error: BaseException) -> None:
        with self._lock:
            self._last_error = error
        LOG.error(
            "PULSE_CAPTURE_FAILED type=%s message=%s",
            type(error).__name__,
            str(error)[:240],
        )
        if self.on_error is not None:
            try:
                self.on_error(error)
            except Exception:
                pass

    def _terminate(self, process: Any) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=self.stop_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self.stop_timeout)
        stderr = getattr(process, "stderr", None)
        if stderr is not None:
            try:
                stderr.close()
            except Exception:
                pass

    def __enter__(self) -> "PulseCapture":
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.stop()


class PulsePlayback:
    """Play streamed PCM through AEC without blocking WebSocket reception.

    Realtime audio deltas arrive in network-sized bursts. A small initial
    jitter buffer absorbs normal packet variation, while a dedicated writer
    keeps slow PulseAudio backpressure away from the Realtime receive loop.
    """

    def __init__(
        self,
        *,
        sink: str = AEC_SINK,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = 1,
        pulse_server: str = PULSE_SERVER,
        prebuffer_msec: int = 100,
        latency_msec: int = 180,
        process_time_msec: int = 20,
        rebuffer_idle_msec: int = 180,
        popen_factory: ProcessFactory = subprocess.Popen,
        stop_timeout: float = 1.0,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        if (
            sample_rate <= 0
            or channels <= 0
            or not pulse_server
            or prebuffer_msec < 0
            or latency_msec <= 0
            or process_time_msec <= 0
            or rebuffer_idle_msec < 0
        ):
            raise ValueError("Pulse playback settings are invalid")
        self.sink = sink
        self.sample_rate = sample_rate
        self.channels = channels
        self.pulse_server = pulse_server
        self.prebuffer_msec = prebuffer_msec
        self.latency_msec = latency_msec
        self.process_time_msec = process_time_msec
        self.rebuffer_idle_msec = rebuffer_idle_msec
        self.popen_factory = popen_factory
        self.stop_timeout = stop_timeout
        self.on_error = on_error
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._process: Any = None
        self._thread: threading.Thread | None = None
        self._buffer = bytearray()
        self._inflight_bytes = 0
        self._generation = 0
        self._primed = False
        self._first_buffered_at: float | None = None
        self._last_enqueued_at: float | None = None
        self._last_error: BaseException | None = None
        self._audible_until = 0.0
        self._closed = False
        self._response_started_at: float | None = None
        self._response_enqueued_bytes = 0
        self._response_delivered_bytes = 0
        self._response_chunks = 0
        self._response_process_starts = 0
        self._response_rebuffers = 0
        self._response_max_gap_ms = 0.0
        self._response_last_chunk_at: float | None = None

        bytes_per_second = self.sample_rate * self.channels * 2
        self._bytes_per_second = bytes_per_second
        frame_bytes = self.channels * 2
        self._prebuffer_bytes = max(
            frame_bytes,
            (bytes_per_second * self.prebuffer_msec) // 1_000,
        )
        self._batch_bytes = max(
            frame_bytes,
            (bytes_per_second * self.process_time_msec) // 1_000,
        )
        self._max_batch_bytes = 4 * self._batch_bytes

    @property
    def command(self) -> list[str]:
        return playback_command(
            sink=self.sink,
            sample_rate=self.sample_rate,
            channels=self.channels,
            latency_msec=self.latency_msec,
            process_time_msec=self.process_time_msec,
        )

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def last_error(self) -> BaseException | None:
        with self._lock:
            return self._last_error

    @property
    def queued_bytes(self) -> int:
        with self._lock:
            return len(self._buffer) + self._inflight_bytes

    @property
    def remaining_seconds(self) -> float:
        """Conservative estimate of local audio still buffered or audible."""

        with self._lock:
            return self._remaining_seconds_locked(time.monotonic())

    @property
    def generated_audio_ms(self) -> int:
        """Duration of PCM received for the current response."""

        with self._lock:
            return round(
                self._response_enqueued_bytes * 1_000 / self._bytes_per_second
            )

    @property
    def played_audio_ms(self) -> int:
        """Conservative lower bound for audio already heard by the user.

        PulseAudio owns an additional server-side queue. Subtracting the
        predicted tail may under-report playback slightly, which is safer than
        truncating assistant context beyond audio the user actually heard.
        """

        with self._lock:
            generated = self._response_enqueued_bytes / self._bytes_per_second
            remaining = self._remaining_seconds_locked(time.monotonic())
            played = min(generated, max(0.0, generated - remaining))
            return round(played * 1_000)

    def begin_response(self) -> None:
        """Reset metadata-only playback counters for one model response."""

        with self._condition:
            self._response_started_at = time.monotonic()
            self._response_enqueued_bytes = 0
            self._response_delivered_bytes = 0
            self._response_chunks = 0
            self._response_process_starts = 0
            self._response_rebuffers = 0
            self._response_max_gap_ms = 0.0
            self._response_last_chunk_at = None

    def diagnostics(self) -> dict[str, int | float | bool]:
        """Return aggregate timing/queue metadata, never PCM content."""

        with self._lock:
            started_at = self._response_started_at
            elapsed_ms = (
                max(0.0, (time.monotonic() - started_at) * 1_000)
                if started_at is not None
                else 0.0
            )
            generated_ms = round(
                self._response_enqueued_bytes * 1_000 / self._bytes_per_second
            )
            remaining = self._remaining_seconds_locked(time.monotonic())
            played_ms = round(
                min(
                    generated_ms / 1_000,
                    max(0.0, generated_ms / 1_000 - remaining),
                )
                * 1_000
            )
            return {
                "chunks": self._response_chunks,
                "enqueued_bytes": self._response_enqueued_bytes,
                "delivered_bytes": self._response_delivered_bytes,
                "generated_ms": generated_ms,
                "played_ms": played_ms,
                "remaining_ms": round(remaining * 1_000),
                "elapsed_ms": round(elapsed_ms, 1),
                "process_starts": self._response_process_starts,
                "rebuffer_events": self._response_rebuffers,
                "max_gap_ms": round(self._response_max_gap_ms, 1),
                "queued_bytes": len(self._buffer) + self._inflight_bytes,
                "process_running": bool(
                    self._process is not None and self._process.poll() is None
                ),
            }

    def log_diagnostics(self, *, reason: str) -> None:
        metrics = self.diagnostics()
        LOG.info(
            "PULSE_PLAYBACK_STREAM reason=%s chunks=%d enqueued_bytes=%d "
            "delivered_bytes=%d generated_ms=%d played_ms=%d remaining_ms=%d "
            "elapsed_ms=%.1f process_starts=%d rebuffer_events=%d "
            "max_gap_ms=%.1f queued_bytes=%d process_running=%s",
            str(reason)[:32],
            metrics["chunks"],
            metrics["enqueued_bytes"],
            metrics["delivered_bytes"],
            metrics["generated_ms"],
            metrics["played_ms"],
            metrics["remaining_ms"],
            metrics["elapsed_ms"],
            metrics["process_starts"],
            metrics["rebuffer_events"],
            metrics["max_gap_ms"],
            metrics["queued_bytes"],
            metrics["process_running"],
        )

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(
                self._buffer
                or self._inflight_bytes
                or time.monotonic() < self._audible_until
            )

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Wait for the predicted Pulse tail, bounded by timeout."""

        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._condition:
            while not self._closed:
                now = time.monotonic()
                queued = bool(self._buffer or self._inflight_bytes)
                tail = max(0.0, self._audible_until - now)
                if not queued and tail <= 0:
                    return True
                if deadline is not None:
                    available = deadline - now
                    if available <= 0:
                        return False
                else:
                    available = max(tail, 0.05)
                wait_for = min(
                    available,
                    max(0.01, min(tail if tail > 0 else 0.05, 0.1)),
                )
                self._condition.wait(timeout=wait_for)
            return True

    def start(self) -> None:
        with self._condition:
            self._ensure_worker_locked()

    def write(self, pcm_audio: bytes) -> None:
        if not isinstance(pcm_audio, bytes):
            raise TypeError("PCM audio must be bytes")
        if not pcm_audio:
            return
        with self._condition:
            if self._closed:
                raise RuntimeError("PulsePlayback is closed")
            self._ensure_worker_locked()
            now = time.monotonic()
            previous_chunk_at = self._response_last_chunk_at
            if previous_chunk_at is not None:
                gap_ms = max(0.0, (now - previous_chunk_at) * 1_000)
                self._response_max_gap_ms = max(
                    self._response_max_gap_ms,
                    gap_ms,
                )
            else:
                gap_ms = 0.0
            self._response_last_chunk_at = now
            no_local_queue = not self._buffer and self._inflight_bytes == 0
            was_idle = no_local_queue and (
                now >= self._audible_until
                or (
                    self._last_enqueued_at is not None
                    and now - self._last_enqueued_at
                    >= self.rebuffer_idle_msec / 1_000
                )
            )
            if was_idle and self._response_chunks:
                self._response_rebuffers += 1
            if was_idle and self._primed:
                # Pulse's predicted tail has drained. Re-arm the initial
                # threshold so a delayed delta cannot resume as a tiny audible
                # fragment followed by another gap.
                self._primed = False
            if not self._buffer:
                self._first_buffered_at = now
            self._last_enqueued_at = now
            self._buffer.extend(pcm_audio)
            self._response_chunks += 1
            self._response_enqueued_bytes += len(pcm_audio)
            self._condition.notify_all()

    def interrupt(self) -> None:
        """Drop buffered and server-queued speech before the next response."""

        with self._condition:
            self._generation += 1
            self._buffer.clear()
            self._inflight_bytes = 0
            self._primed = False
            self._first_buffered_at = None
            self._last_enqueued_at = None
            self._audible_until = 0.0
            process, self._process = self._process, None
            self._condition.notify_all()
        if process is not None:
            self._terminate(process)

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._buffer.clear()
            self._inflight_bytes = 0
            self._audible_until = 0.0
            process, self._process = self._process, None
            thread = self._thread
            self._condition.notify_all()
        if process is not None:
            self._terminate(process)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(self.stop_timeout, 0.1))
        with self._condition:
            if self._thread is thread:
                self._thread = None

    def _ensure_worker_locked(self) -> None:
        if self._closed:
            raise RuntimeError("PulsePlayback is closed")
        if self._thread is not None and self._thread.is_alive():
            return
        thread = threading.Thread(
            target=self._write_loop,
            name="nexo-pulse-playback",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _ensure_process(self, generation: int) -> Any | None:
        with self._condition:
            if self._closed or generation != self._generation:
                return None
            if self._process is not None and self._process.poll() is None:
                return self._process
        process = self.popen_factory(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
            shell=False,
            env=_pulse_environment(self.pulse_server),
        )
        if process.stdin is None:
            self._terminate(process)
            raise RuntimeError("pacat did not expose an audio input stream")
        if process.poll() is not None:
            error = _process_failure(process, "pacat")
            self._terminate(process)
            raise error
        with self._condition:
            if self._closed or generation != self._generation:
                self._terminate(process)
                return None
            self._process = process
            self._response_process_starts += 1
        return process

    def _write_loop(self) -> None:
        try:
            while True:
                item = self._next_batch()
                if item is None:
                    return
                generation, batch = item
                try:
                    self._deliver(generation, batch)
                finally:
                    with self._condition:
                        self._inflight_bytes = max(
                            0,
                            self._inflight_bytes - len(batch),
                        )
                        self._condition.notify_all()
        finally:
            with self._condition:
                if self._thread is threading.current_thread():
                    self._thread = None

    def _next_batch(self) -> tuple[int, bytes] | None:
        with self._condition:
            while not self._closed:
                now = time.monotonic()
                if not self._buffer:
                    if (
                        self._primed
                        and self._last_enqueued_at is not None
                        and now >= self._audible_until
                        and now - self._last_enqueued_at
                        >= self.rebuffer_idle_msec / 1_000
                    ):
                        self._primed = False
                    self._condition.wait(timeout=0.05)
                    continue

                if not self._primed:
                    started = self._first_buffered_at or now
                    remaining = self.prebuffer_msec / 1_000 - (now - started)
                    if len(self._buffer) < self._prebuffer_bytes and remaining > 0:
                        self._condition.wait(timeout=remaining)
                        continue
                    self._primed = True
                elif len(self._buffer) < self._batch_bytes:
                    received = self._last_enqueued_at or now
                    remaining = self.process_time_msec / 2_000 - (now - received)
                    if remaining > 0:
                        self._condition.wait(timeout=remaining)
                        continue

                size = min(len(self._buffer), self._max_batch_bytes)
                batch = bytes(self._buffer[:size])
                del self._buffer[:size]
                self._inflight_bytes += size
                if not self._buffer:
                    self._first_buffered_at = None
                return self._generation, batch
            return None

    def _deliver(self, generation: int, batch: bytes) -> None:
        last_error: BaseException | None = None
        for _attempt in range(2):
            try:
                process = self._ensure_process(generation)
                if process is None:
                    return
                if process.poll() is not None:
                    raise _process_failure(process, "pacat")
                self._write_all(process.stdin, batch)
                with self._condition:
                    if generation == self._generation:
                        delivered_at = time.monotonic()
                        startup = (
                            self.latency_msec / 1_000
                            if delivered_at >= self._audible_until
                            else 0.0
                        )
                        self._audible_until = max(
                            self._audible_until,
                            delivered_at + startup,
                        ) + len(batch) / self._bytes_per_second
                        self._response_delivered_bytes += len(batch)
                        self._condition.notify_all()
                return
            except (BrokenPipeError, OSError, RuntimeError) as error:
                last_error = error
                with self._condition:
                    process, self._process = self._process, None
                if process is not None:
                    self._terminate(process)
                with self._condition:
                    if self._closed or generation != self._generation:
                        return
        if last_error is not None:
            self._report_error(last_error)

    @staticmethod
    def _write_all(stream: BinaryIO, pcm_audio: bytes) -> None:
        remaining = memoryview(pcm_audio)
        while remaining:
            written = stream.write(remaining)
            if written is None:
                return
            if written <= 0:
                raise BrokenPipeError("pacat accepted no PCM data")
            remaining = remaining[written:]

    def _report_error(self, error: BaseException) -> None:
        with self._condition:
            self._last_error = error
            self._audible_until = 0.0
            self._condition.notify_all()
        LOG.error(
            "PULSE_PLAYBACK_FAILED type=%s message=%s",
            type(error).__name__,
            str(error)[:240],
        )
        if self.on_error is not None:
            try:
                self.on_error(error)
            except Exception:
                pass

    def _remaining_seconds_locked(self, now: float) -> float:
        """Predict unheard PCM from Pulse delivery time plus local queues."""

        remaining = max(0.0, self._audible_until - now)
        queued_bytes = len(self._buffer) + self._inflight_bytes
        if queued_bytes:
            remaining += queued_bytes / self._bytes_per_second
            if not self._primed and self._first_buffered_at is not None:
                prebuffer_left = (
                    self.prebuffer_msec / 1_000
                    - (now - self._first_buffered_at)
                )
                remaining += max(0.0, prebuffer_left)
        return remaining

    def _terminate(self, process: Any) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=self.stop_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self.stop_timeout)
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, name, None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    def __enter__(self) -> "PulsePlayback":
        self.start()
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()


__all__ = [
    "AEC_SINK",
    "AEC_SOURCE",
    "PULSE_SERVER",
    "PulseCapture",
    "PulsePlayback",
    "capture_command",
    "playback_command",
]
