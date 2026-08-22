"""Faster Whisper STT with VAD and signal-based hallucination rejection."""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
from ovos_stt_plugin_fasterwhisper import FasterWhisperSTT

from .stt_quality import (
    DEFAULT_QUALITY_GATE,
    DEFAULT_VAD_PARAMETERS,
    assess_speech_quality,
)


LOG = logging.getLogger("nexo.stt")


class NexoSmartSTT(FasterWhisperSTT):
    """Transcribe only when the audio and Whisper agree that speech exists."""

    sample_rate = 16_000

    @staticmethod
    def _clean_audio_array(audio: Any) -> np.ndarray:
        # AudioData.get_raw_data removes the WAV header and performs the same
        # 16 kHz/16-bit conversion expected by Whisper.
        raw = audio.get_raw_data(convert_rate=16_000, convert_width=2)
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    def _recognize(self, audio: Any, language: str | None = None) -> tuple[str, float]:
        lang = language or self.lang
        if lang == "auto":
            lang, _ = self.detect_language(audio)

        samples = self._clean_audio_array(audio)
        audio_seconds = len(samples) / self.sample_rate
        rms = math.sqrt(float(np.mean(np.square(samples)))) if samples.size else 0.0
        if samples.size == 0:
            LOG.warning("NEXO_STT accepted=false reason=empty_audio")
            return "", 0.0

        vad_parameters = dict(DEFAULT_VAD_PARAMETERS)
        configured_vad = self.config.get("vad_parameters", {})
        if isinstance(configured_vad, dict):
            vad_parameters.update(configured_vad)

        segments_iter, info = self.engine.transcribe(
            samples,
            beam_size=self.beam_size,
            condition_on_previous_text=False,
            language=lang.split("-")[0].lower(),
            vad_filter=True,
            vad_parameters=vad_parameters,
            hallucination_silence_threshold=self.config.get(
                "hallucination_silence_threshold", 1.0
            ),
        )
        segments = list(segments_iter)
        text = "".join(segment.text for segment in segments).strip()
        if not segments or not text:
            LOG.info(
                "NEXO_STT accepted=false reason=no_speech_after_vad audio_s=%.3f rms=%.5f",
                audio_seconds,
                rms,
            )
            return "", 0.0

        weights = [max(0.05, float(segment.end) - float(segment.start)) for segment in segments]
        total_weight = sum(weights)
        speech_seconds = max(
            float(getattr(info, "duration_after_vad", 0.0) or 0.0),
            sum(max(0.0, float(segment.end) - float(segment.start)) for segment in segments),
        )
        average_log_probability = sum(
            float(segment.avg_logprob) * weight
            for segment, weight in zip(segments, weights)
        ) / total_weight
        no_speech_probability = sum(
            float(segment.no_speech_prob) * weight
            for segment, weight in zip(segments, weights)
        ) / total_weight
        compression_ratio = max(float(segment.compression_ratio) for segment in segments)

        quality = assess_speech_quality(
            audio_seconds=audio_seconds,
            speech_seconds=speech_seconds,
            average_log_probability=average_log_probability,
            no_speech_probability=no_speech_probability,
            compression_ratio=compression_ratio,
            settings=self.config.get("quality_gate", DEFAULT_QUALITY_GATE),
        )
        LOG.info(
            "NEXO_STT accepted=%s reason=%s audio_s=%.3f speech_s=%.3f "
            "avg_logprob=%.3f no_speech=%.3f confidence=%.3f rms=%.5f",
            str(quality.accepted).lower(),
            quality.reason,
            audio_seconds,
            speech_seconds,
            average_log_probability,
            no_speech_probability,
            quality.confidence,
            rms,
        )
        return (text, quality.confidence) if quality.accepted else ("", 0.0)

    def execute(self, audio: Any, language: str | None = None) -> str:
        return self._recognize(audio, language)[0]

    def transcribe(self, audio: Any, lang: str | None = None) -> list[tuple[str, float]]:
        text, confidence = self._recognize(audio, lang)
        return [(text, confidence)] if text else []


NexoSmartSTTConfig = {
    "model": "large-v3-turbo",
    "use_cuda": True,
    "compute_type": "float16",
    "beam_size": 5,
    "vad_filter": True,
    "vad_parameters": dict(DEFAULT_VAD_PARAMETERS),
    "quality_gate": dict(DEFAULT_QUALITY_GATE),
}
