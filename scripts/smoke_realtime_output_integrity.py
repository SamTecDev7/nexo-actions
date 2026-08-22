#!/usr/bin/env python3
"""Compare Realtime PCM with what reaches WSLg's speaker monitor.

This is an explicit, audible diagnostic.  It generates one bounded sentence,
plays it through the production ``PulsePlayback`` path, and captures
``RDPSink.monitor``.  Raw audio and transcripts are kept only in memory and
are never printed or written to disk.

The result is deliberately conservative: low correlation, weak output, or
competing system audio makes the probe inconclusive instead of producing a
false pass/fail verdict.
"""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
from typing import Any

import numpy as np

from nexo_actions.realtime.pulse import PulseCapture, PulsePlayback
from nexo_actions.realtime.session import RealtimeSession


DEFAULT_PHRASE = (
    "Diga exatamente esta frase, em português do Brasil, com ritmo natural e "
    "contínuo: Quando a tecnologia funciona bem, cada resposta chega com "
    "clareza, ritmo e continuidade. O Nexo acompanha a conversa, entende o "
    "contexto e executa a tarefa certa com uma voz estável e agradável."
)


def _pcm16_mono(pcm: bytes, channels: int) -> np.ndarray:
    if channels <= 0:
        raise ValueError("channels must be positive")
    usable = len(pcm) - (len(pcm) % (2 * channels))
    if usable <= 0:
        return np.empty(0, dtype=np.float32)
    samples = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples


def _rms_envelope(
    pcm: bytes,
    *,
    sample_rate: int,
    channels: int,
    frame_ms: int,
) -> np.ndarray:
    samples = _pcm16_mono(pcm, channels)
    frame_samples = max(1, round(sample_rate * frame_ms / 1_000))
    frame_count = samples.size // frame_samples
    if frame_count <= 0:
        return np.empty(0, dtype=np.float64)
    framed = samples[: frame_count * frame_samples].reshape(
        frame_count, frame_samples
    )
    return np.sqrt(np.mean(framed.astype(np.float64) ** 2, axis=1))


def _moving_max(values: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0 or values.size == 0:
        return values.copy()
    padded = np.pad(values, (radius, radius), mode="edge")
    return np.maximum.reduce(
        [padded[offset : offset + values.size] for offset in range(2 * radius + 1)]
    )


def _best_normalized_alignment(
    captured: np.ndarray,
    generated: np.ndarray,
) -> tuple[int, float]:
    """Return the capture frame where ``generated`` best starts and its r."""

    if generated.size < 3 or captured.size < generated.size:
        return 0, 0.0
    reference = generated - generated.mean()
    reference_energy = float(np.dot(reference, reference))
    if reference_energy <= 1e-12:
        return 0, 0.0

    width = generated.size
    prefix = np.concatenate(([0.0], np.cumsum(captured, dtype=np.float64)))
    prefix_sq = np.concatenate(
        ([0.0], np.cumsum(captured * captured, dtype=np.float64))
    )
    window_sum = prefix[width:] - prefix[:-width]
    window_sq = prefix_sq[width:] - prefix_sq[:-width]
    window_energy = np.maximum(window_sq - (window_sum * window_sum) / width, 0.0)
    numerator = np.correlate(captured, reference, mode="valid")
    denominator = np.sqrt(window_energy * reference_energy)
    scores = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator, dtype=np.float64),
        where=denominator > 1e-12,
    )
    best = int(np.argmax(scores))
    return best, float(np.clip(scores[best], -1.0, 1.0))


def _runs(mask: np.ndarray, *, frame_ms: int, minimum_ms: int) -> list[dict[str, int]]:
    result: list[dict[str, int]] = []
    start: int | None = None
    for index, active in enumerate(np.append(mask, False)):
        if bool(active) and start is None:
            start = index
        elif not bool(active) and start is not None:
            duration_ms = (index - start) * frame_ms
            if duration_ms >= minimum_ms:
                result.append(
                    {
                        "start_ms": start * frame_ms,
                        "duration_ms": duration_ms,
                    }
                )
            start = None
    return result


def analyze_output_integrity(
    generated_pcm: bytes,
    captured_pcm: bytes,
    *,
    generated_rate: int = 24_000,
    captured_rate: int = 44_100,
    captured_channels: int = 2,
    frame_ms: int = 10,
) -> dict[str, Any]:
    """Align aggregate energy and find output-only gaps of at least 20 ms."""

    generated = _rms_envelope(
        generated_pcm,
        sample_rate=generated_rate,
        channels=1,
        frame_ms=frame_ms,
    )
    captured = _rms_envelope(
        captured_pcm,
        sample_rate=captured_rate,
        channels=captured_channels,
        frame_ms=frame_ms,
    )
    base: dict[str, Any] = {
        "analysis_version": 1,
        "generated_duration_ms": int(generated.size * frame_ms),
        "captured_duration_ms": int(captured.size * frame_ms),
        "frame_ms": frame_ms,
        "conclusive": False,
        "integrity_ok": None,
        "reason": None,
        "alignment_ms": None,
        "alignment_correlation": 0.0,
        "output_snr_db": None,
        "possible_competing_audio": False,
        "voiced_coverage": None,
        "extra_gap_count": None,
        "extra_gap_total_ms": None,
        "max_extra_gap_ms": None,
        "extra_gaps": [],
    }
    if generated.size < max(30, 1_000 // frame_ms):
        base["reason"] = "generated_audio_too_short"
        return base
    if captured.size < generated.size:
        base["reason"] = "capture_shorter_than_generated_audio"
        return base

    generated_floor = float(np.percentile(generated, 10))
    captured_floor = float(np.percentile(captured, 10))
    generated_signal = np.maximum(generated - generated_floor, 0.0)
    captured_signal = np.maximum(captured - captured_floor, 0.0)
    generated_level = float(np.percentile(generated_signal, 95))
    captured_level = float(np.percentile(captured_signal, 95))
    if generated_level < 8.0:
        base["reason"] = "generated_audio_has_no_reliable_signal"
        return base
    if captured_level < 8.0:
        base["reason"] = "captured_output_has_no_reliable_signal"
        return base

    generated_feature = np.log1p(9.0 * generated_signal / generated_level)
    captured_feature = np.log1p(9.0 * captured_signal / captured_level)
    alignment, correlation = _best_normalized_alignment(
        captured_feature,
        generated_feature,
    )
    base["alignment_ms"] = alignment * frame_ms
    base["alignment_correlation"] = round(correlation, 4)
    if alignment + generated.size > captured.size:
        base["reason"] = "aligned_capture_is_clipped"
        return base

    aligned = captured_signal[alignment : alignment + generated.size]
    output_snr = 20.0 * math.log10(
        max(captured_level, 1.0) / max(captured_floor, 1.0)
    )
    base["output_snr_db"] = round(output_snr, 1)

    outside = np.concatenate(
        (
            captured_signal[: max(0, alignment - 5)],
            captured_signal[min(captured.size, alignment + generated.size + 5) :],
        )
    )
    outside_level = float(np.percentile(outside, 95)) if outside.size else 0.0
    possible_competing_audio = outside_level > captured_level * 0.30
    base["possible_competing_audio"] = possible_competing_audio

    strong = generated_signal > generated_level * 0.16
    edge_frames = max(1, 80 // frame_ms)
    strong[:edge_frames] = False
    strong[-edge_frames:] = False
    if int(strong.sum()) < max(20, 500 // frame_ms):
        base["reason"] = "generated_audio_has_too_few_voiced_frames"
        return base

    ratio_mask = strong & (aligned > captured_level * 0.015)
    if int(ratio_mask.sum()) < 10:
        base["reason"] = "captured_output_cannot_be_scaled"
        return base
    scale = float(np.median(aligned[ratio_mask] / generated_signal[ratio_mask]))
    if not math.isfinite(scale) or scale <= 1e-6:
        base["reason"] = "captured_output_scale_is_invalid"
        return base

    captured_equivalent = aligned / scale
    # One frame of tolerance absorbs envelope-frame boundaries and tiny clock
    # differences without hiding a perceptible (20+ ms) discontinuity.
    tolerant_output = _moving_max(captured_equivalent, radius=1)
    covered = tolerant_output[strong] >= generated_signal[strong] * 0.12
    voiced_coverage = float(np.mean(covered))
    base["voiced_coverage"] = round(voiced_coverage, 4)

    missing = strong & (tolerant_output < generated_signal * 0.12)
    gaps = _runs(missing, frame_ms=frame_ms, minimum_ms=20)
    base["extra_gaps"] = gaps[:20]
    base["extra_gap_count"] = len(gaps)
    base["extra_gap_total_ms"] = sum(item["duration_ms"] for item in gaps)
    base["max_extra_gap_ms"] = max(
        (item["duration_ms"] for item in gaps),
        default=0,
    )

    if correlation < 0.60:
        base["reason"] = "alignment_correlation_too_low"
        return base
    if output_snr < 15.0:
        base["reason"] = "captured_output_snr_too_low"
        return base
    if possible_competing_audio:
        base["reason"] = "competing_audio_detected"
        return base

    base["conclusive"] = True
    base["integrity_ok"] = bool(
        base["max_extra_gap_ms"] <= 30
        and base["extra_gap_total_ms"] <= generated.size * frame_ms * 0.005
        and voiced_coverage >= 0.985
    )
    base["reason"] = "ok" if base["integrity_ok"] else "extra_output_gaps_detected"
    return base


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-realtime-2.1")
    parser.add_argument("--voice", default="marin")
    parser.add_argument("--phrase", default=DEFAULT_PHRASE)
    parser.add_argument("--capture-source", default="RDPSink.monitor")
    parser.add_argument("--capture-server", default="unix:/mnt/wslg/PulseServer")
    parser.add_argument("--timeout", type=float, default=45.0)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    generated_pcm = bytearray()
    captured_pcm = bytearray()
    capture_lock = threading.Lock()
    session_ready = threading.Event()
    response_done = threading.Event()
    errors: list[str] = []
    max_capture_bytes = 32 * 1024 * 1024

    player = PulsePlayback()
    capture = PulseCapture(
        source=args.capture_source,
        sample_rate=44_100,
        channels=2,
        chunk_frames=2_205,
        pulse_server=args.capture_server,
    )

    def on_capture(chunk: bytes) -> None:
        with capture_lock:
            available = max_capture_bytes - len(captured_pcm)
            if available > 0:
                captured_pcm.extend(chunk[:available])

    def on_audio(chunk: bytes, _event: Any) -> None:
        generated_pcm.extend(chunk)
        player.write(chunk)

    def on_event(event: Any) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "session.updated":
            session_ready.set()
        elif event_type == "response.done":
            response_done.set()

    def on_error(error: BaseException) -> None:
        errors.append(f"{type(error).__name__}: {str(error)[:240]}")

    session = RealtimeSession(
        model=args.model,
        voice=args.voice,
        instructions=(
            "Fale em português do Brasil. Neste diagnóstico, leia somente o "
            "texto solicitado, sem comentários antes ou depois."
        ),
        automatic_response=False,
        automatic_interruption=False,
        max_output_tokens=4_096,
        on_audio=on_audio,
        on_event=on_event,
        on_error=on_error,
        max_reconnects=0,
        connect_timeout=15,
    )

    started_capture = False
    try:
        session.start()
        if not session.wait_until_connected(timeout=15):
            raise TimeoutError("Realtime connection did not become ready")
        if not session_ready.wait(timeout=15):
            raise TimeoutError("Realtime session.update was not acknowledged")

        capture.start(on_capture)
        started_capture = True
        time.sleep(0.4)
        if capture.last_error is not None:
            raise RuntimeError(str(capture.last_error))

        player.begin_response()
        session.send_text(args.phrase)
        if not response_done.wait(timeout=max(1.0, args.timeout)):
            raise TimeoutError("Realtime response did not finish before the timeout")
        drained = player.wait_until_idle(timeout=max(5.0, args.timeout))
        time.sleep(0.4)
        capture.stop()
        started_capture = False
        with capture_lock:
            capture_bytes = bytes(captured_pcm)

        analysis = analyze_output_integrity(bytes(generated_pcm), capture_bytes)
        payload = {
            "ok": bool(
                not errors
                and drained
                and player.last_error is None
                and capture.last_error is None
                and analysis["conclusive"]
                and analysis["integrity_ok"]
            ),
            "audible_probe": True,
            "raw_audio_persisted": False,
            "response_done": response_done.is_set(),
            "playback_drained": drained,
            "generated_audio_bytes": len(generated_pcm),
            "captured_audio_bytes": len(capture_bytes),
            "realtime_timing": session.audio_diagnostics(),
            "playback_timing": player.diagnostics(),
            "integrity": analysis,
            "errors": errors,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0 if payload["ok"] else 1
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {str(exc)[:240]}")
        print(
            json.dumps(
                {
                    "ok": False,
                    "audible_probe": True,
                    "raw_audio_persisted": False,
                    "generated_audio_bytes": len(generated_pcm),
                    "captured_audio_bytes": len(captured_pcm),
                    "errors": errors,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    finally:
        if started_capture:
            capture.stop()
        session.close()
        player.close()


if __name__ == "__main__":
    raise SystemExit(main())
