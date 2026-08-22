"""Signal-based defaults and quality decisions for Nexo speech recognition."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping


SMART_STT_MODULE = "nexo-smart-stt"
LEGACY_STT_MODULE = "ovos-stt-plugin-fasterwhisper"

# The audible acknowledgement must finish before OVOS starts looking for the
# user's speech.  This keeps the prompt and the AEC resynchronisation transient
# out of the command while still allowing a natural response immediately after
# "Manda".
SMART_LISTENER_SETTINGS: dict[str, Any] = {
    "instant_listen": False,
    "speech_begin": 0.30,
    "silence_end": 0.90,
    "recording_timeout_with_silence": 5.0,
}

DEFAULT_VAD_PARAMETERS: dict[str, Any] = {
    "threshold": 0.50,
    "min_speech_duration_ms": 250,
    "min_silence_duration_ms": 450,
    "speech_pad_ms": 180,
}

DEFAULT_QUALITY_GATE: dict[str, float] = {
    # With the listener timings above, even a genuine one-word command contains
    # the spoken word plus the trailing-silence window.  A shorter recording is
    # an interrupted capture, not a usable utterance.
    "minimum_audio_seconds": 1.00,
    "minimum_speech_seconds": 0.24,
    "short_audio_seconds": 1.45,
    "minimum_short_speech_seconds": 0.30,
    "minimum_average_log_probability": -0.90,
    "minimum_short_average_log_probability": -0.68,
    "maximum_no_speech_probability": 0.68,
    "maximum_short_no_speech_probability": 0.48,
    "maximum_compression_ratio": 2.40,
}


def _mapping(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def active_stt_settings(ovos_config: Mapping[str, Any]) -> dict[str, Any]:
    """Return Nexo STT settings while remaining compatible with old configs."""
    root = ovos_config.get("stt", {})
    if not isinstance(root, Mapping):
        return {}
    module = str(root.get("module") or "")
    if module == SMART_STT_MODULE and isinstance(root.get(SMART_STT_MODULE), Mapping):
        return _mapping(root[SMART_STT_MODULE])
    if isinstance(root.get(SMART_STT_MODULE), Mapping):
        return _mapping(root[SMART_STT_MODULE])
    return _mapping(root.get(LEGACY_STT_MODULE))


def configure_smart_stt(
    stt_root: MutableMapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Migrate a Faster Whisper config to Nexo without losing user choices."""
    current = _mapping(stt_root.get(SMART_STT_MODULE))
    if not current:
        current = _mapping(stt_root.get(LEGACY_STT_MODULE))
    current.update(_mapping(overrides))
    current["vad_filter"] = True

    vad_parameters = _mapping(current.get("vad_parameters"))
    for key, value in DEFAULT_VAD_PARAMETERS.items():
        vad_parameters.setdefault(key, value)
    current["vad_parameters"] = vad_parameters

    quality_gate = _mapping(current.get("quality_gate"))
    for key, value in DEFAULT_QUALITY_GATE.items():
        quality_gate.setdefault(key, value)
    current["quality_gate"] = quality_gate

    stt_root["module"] = SMART_STT_MODULE
    stt_root[SMART_STT_MODULE] = current
    return current


@dataclass(frozen=True)
class SpeechQuality:
    accepted: bool
    reason: str
    confidence: float


def assess_speech_quality(
    *,
    audio_seconds: float,
    speech_seconds: float,
    average_log_probability: float,
    no_speech_probability: float,
    compression_ratio: float,
    settings: Mapping[str, Any] | None = None,
) -> SpeechQuality:
    """Decide from acoustic/decoder evidence, never from matching phrases."""
    gate = dict(DEFAULT_QUALITY_GATE)
    if isinstance(settings, Mapping):
        for key in gate:
            if key in settings:
                gate[key] = float(settings[key])

    acoustic_confidence = math.exp(min(0.0, float(average_log_probability)))
    silence_confidence = 1.0 - max(0.0, min(1.0, float(no_speech_probability)))
    confidence = max(0.0, min(1.0, 0.55 * acoustic_confidence + 0.45 * silence_confidence))

    if audio_seconds < gate["minimum_audio_seconds"]:
        return SpeechQuality(False, "capture_too_short", confidence)
    if speech_seconds < gate["minimum_speech_seconds"]:
        return SpeechQuality(False, "insufficient_speech", confidence)
    if compression_ratio > gate["maximum_compression_ratio"]:
        return SpeechQuality(False, "decoder_repetition", confidence)
    if average_log_probability < gate["minimum_average_log_probability"]:
        return SpeechQuality(False, "low_acoustic_probability", confidence)
    if no_speech_probability > gate["maximum_no_speech_probability"]:
        return SpeechQuality(False, "probable_silence", confidence)

    if audio_seconds < gate["short_audio_seconds"]:
        if speech_seconds < gate["minimum_short_speech_seconds"]:
            return SpeechQuality(False, "short_capture_without_sustained_voice", confidence)
        if average_log_probability < gate["minimum_short_average_log_probability"]:
            return SpeechQuality(False, "short_capture_low_probability", confidence)
        if no_speech_probability > gate["maximum_short_no_speech_probability"]:
            return SpeechQuality(False, "short_capture_probable_silence", confidence)

    return SpeechQuality(True, "accepted", confidence)
