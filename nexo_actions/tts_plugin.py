"""OVOS TTS bridge for Nexo's local neural voice with a Piper fallback."""

from __future__ import annotations

import io
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ovos_plugin_manager.templates.tts import TTS, TTSValidator
from ovos_utils import classproperty
from ovos_utils.log import LOG


class NexoHybridTTS(TTS):
    """Use the isolated neural runtime and fail over locally without silence."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, audio_ext="wav", validator=NexoHybridTTSValidator(self))
        self._fallback_voice = None

    @property
    def endpoint(self) -> str:
        return str(self.config.get("endpoint", "http://127.0.0.1:8791/v2/synthesize"))

    def _neural_audio(self, sentence: str, voice: str) -> bytes:
        params = urllib.parse.urlencode({
            "utterance": sentence,
            "lang": self.lang or "pt-BR",
            "voice": voice,
        })
        request = urllib.request.Request(
            f"{self.endpoint}?{params}",
            headers={"Accept": "audio/wav", "User-Agent": "Nexo-OVOS/1.0"},
        )
        timeout = max(10, min(180, int(self.config.get("request_timeout", 120))))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024 or not data.startswith(b"RIFF"):
            raise RuntimeError("O motor neural devolveu áudio inválido")
        return data

    def _fallback_audio(self, sentence: str) -> bytes:
        from phoonnx.config import SynthesisConfig
        from phoonnx.voice import TTSVoice

        model = Path(str(self.config.get("fallback_model") or "")).expanduser()
        config = Path(str(self.config.get("fallback_config") or "")).expanduser()
        if not model.is_file() or not config.is_file():
            raise RuntimeError("A voz neural falhou e o fallback Piper não está configurado")
        if self._fallback_voice is None:
            self._fallback_voice = TTSVoice.load(str(model), str(config))
        scale = max(0.55, min(1.8, float(self.config.get("fallback_length_scale", 0.85))))
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            self._fallback_voice.synthesize_wav(
                sentence,
                wav,
                SynthesisConfig(length_scale=scale),
            )
        return output.getvalue()

    def get_tts(
        self,
        sentence,
        wav_file,
        lang: Optional[str] = None,
        voice: Optional[str] = None,
    ) -> Tuple[Any, None]:
        selected = str(voice or self.voice or self.config.get("voice") or "neural:ptbr-natural")
        try:
            data = self._neural_audio(str(sentence), selected)
        except Exception as exc:
            LOG.error("NEXO_TTS neural_failed=true fallback=piper error=%s", exc)
            data = self._fallback_audio(str(sentence))
        with open(wav_file, "wb") as output:
            output.write(data)
        return wav_file, None

    @classproperty
    def available_languages(self) -> set:
        return {"pt", "pt-BR"}


class NexoHybridTTSValidator(TTSValidator):
    def validate_lang(self) -> None:
        return

    def validate_connection(self) -> None:
        # The plugin deliberately starts with a healthy local fallback even
        # while the optional neural service is being installed.
        return

    def get_tts_class(self):
        return NexoHybridTTS


NexoHybridTTSConfig: Dict[Any, Any] = {}
