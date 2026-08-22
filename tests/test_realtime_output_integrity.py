from __future__ import annotations

import unittest

import numpy as np

from scripts.smoke_realtime_output_integrity import analyze_output_integrity


def _pcm(samples: np.ndarray, *, channels: int = 1) -> bytes:
    clipped = np.clip(np.rint(samples), -32_768, 32_767).astype("<i2")
    if channels > 1:
        clipped = np.repeat(clipped[:, None], channels, axis=1)
    return clipped.tobytes()


def _speech_like(rate: int = 24_000, seconds: float = 7.0) -> np.ndarray:
    time = np.arange(round(rate * seconds), dtype=np.float64) / rate
    envelope = (
        0.42
        + 0.24 * np.sin(2 * np.pi * 1.31 * time)
        + 0.12 * np.sin(2 * np.pi * 3.73 * time + 0.4)
    )
    envelope = np.maximum(envelope, 0.04)
    envelope *= np.where((time % 1.17) < 0.98, 1.0, 0.03)
    carrier = np.sin(2 * np.pi * 211 * time) + 0.35 * np.sin(2 * np.pi * 431 * time)
    return 12_000 * envelope * carrier


def _capture_from_generated(
    generated: np.ndarray,
    *,
    generated_rate: int = 24_000,
    captured_rate: int = 44_100,
    delay_seconds: float = 0.37,
    tail_seconds: float = 0.45,
) -> np.ndarray:
    duration = generated.size / generated_rate
    target_time = np.arange(round(duration * captured_rate)) / captured_rate
    source_time = np.arange(generated.size) / generated_rate
    resampled = np.interp(target_time, source_time, generated)
    return np.concatenate(
        (
            np.zeros(round(delay_seconds * captured_rate)),
            resampled * 0.71,
            np.zeros(round(tail_seconds * captured_rate)),
        )
    )


class RealtimeOutputIntegrityTests(unittest.TestCase):
    def test_integrity_accepts_clean_resampled_output(self) -> None:
        generated = _speech_like()
        captured = _capture_from_generated(generated)

        result = analyze_output_integrity(
            _pcm(generated),
            _pcm(captured, channels=2),
        )

        self.assertIs(result["conclusive"], True)
        self.assertIs(result["integrity_ok"], True)
        self.assertGreater(result["alignment_correlation"], 0.95)
        self.assertEqual(result["extra_gap_count"], 0)

    def test_integrity_detects_inserted_output_dropout(self) -> None:
        generated = _speech_like()
        captured = _capture_from_generated(generated)
        capture_rate = 44_100
        start = round((0.37 + 3.25) * capture_rate)
        captured[start : start + round(0.09 * capture_rate)] = 0

        result = analyze_output_integrity(
            _pcm(generated),
            _pcm(captured, channels=2),
        )

        self.assertIs(result["conclusive"], True)
        self.assertIs(result["integrity_ok"], False)
        self.assertGreaterEqual(result["extra_gap_count"], 1)
        self.assertGreaterEqual(result["max_extra_gap_ms"], 40)

    def test_integrity_refuses_unrelated_capture(self) -> None:
        generated = _speech_like()
        random = np.random.default_rng(17)
        captured = random.normal(0, 2_500, size=round(8.0 * 44_100))

        result = analyze_output_integrity(
            _pcm(generated),
            _pcm(captured, channels=2),
        )

        self.assertIs(result["conclusive"], False)
        self.assertIsNone(result["integrity_ok"])
        self.assertIn(
            result["reason"],
            {
                "alignment_correlation_too_low",
                "captured_output_snr_too_low",
                "competing_audio_detected",
            },
        )
