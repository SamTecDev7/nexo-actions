"""Isolated, lazy-loaded Brazilian Portuguese neural TTS runtime."""

from __future__ import annotations

import argparse
import gc
import io
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


LOG = logging.getLogger("nexo.neural_tts")
DEFAULT_ROOT = Path.home() / ".local" / "share" / "nexo" / "neural-tts"
PROFILES = {
    "neural:ptbr-natural": {
        "label": "Alex Neural BR · Natural e rápida",
        "engine": "kokoro",
        "voice": "pm_alex.pt",
        "speed": 1.0,
    },
    "neural:ptbr-calm": {
        "label": "Dora Neural BR · Serena e rápida",
        "engine": "kokoro",
        "voice": "pf_dora.pt",
        "speed": 0.96,
    },
    "neural:ptbr-expressive": {
        "label": "Orion Neural BR · Expressiva e rápida",
        "engine": "kokoro",
        "voice": "pm_santa.pt",
        "speed": 1.02,
    },
    "neural:ptbr-studio": {
        "label": "Aurora Neural BR · Qualidade estúdio",
        "engine": "chatterbox",
        "prompt": "pt_br_f2.wav",
        "exaggeration": 0.50,
        "temperature": 0.72,
        "cfg_weight": 0.45,
        "seed": 2207,
    },
}


def _clean_for_speech(text: str) -> str:
    value = " ".join(str(text).split()).strip()
    value = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", value)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"[*_`#>]", "", value)
    return " ".join(value.split())[:1200]


def _chunks(text: str, maximum: int = 270) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    output: list[str] = []
    current = ""
    for sentence in sentences:
        words = sentence.split()
        parts: list[str] = []
        part = ""
        for word in words:
            if len(word) > maximum:
                if part:
                    parts.append(part)
                    part = ""
                parts.extend(
                    word[index:index + maximum]
                    for index in range(0, len(word), maximum)
                )
                continue
            candidate_word = f"{part} {word}".strip()
            if part and len(candidate_word) > maximum:
                parts.append(part)
                part = word
            else:
                part = candidate_word
        if part:
            parts.append(part)
        for part in parts:
            candidate = f"{current} {part}".strip()
            if current and len(candidate) > maximum:
                output.append(current)
                current = part
            else:
                current = candidate
    if current:
        output.append(current)
    return output


def _join_audio_segments(segments, sample_rate: int, crossfade_ms: float = 10.0):
    """Join model segments without hard sample discontinuities/clicks."""
    import numpy as np

    usable = [
        np.asarray(segment, dtype=np.float32).reshape(-1)
        for segment in segments
        if segment is not None and np.asarray(segment).size
    ]
    if not usable:
        return np.zeros(1, dtype=np.float32)
    joined = usable[0].copy()
    requested = max(1, int(float(sample_rate) * crossfade_ms / 1000.0))
    for segment in usable[1:]:
        overlap = min(requested, len(joined), len(segment))
        if overlap <= 1:
            joined = np.concatenate((joined, segment))
            continue
        fade_in = np.linspace(0.0, 1.0, overlap, dtype=np.float32)
        blended = joined[-overlap:] * (1.0 - fade_in) + segment[:overlap] * fade_in
        joined = np.concatenate((joined[:-overlap], blended, segment[overlap:]))
    return joined


class NeuralRuntime:
    def __init__(self, root: Path = DEFAULT_ROOT) -> None:
        self.root = Path(root).expanduser()
        self.source_root = self.root / "source"
        self.model_root = self.root / "assembled"
        self.kokoro_root = self.root / "kokoro"
        self.prompt_root = self.root / "prompts"
        self.marker = self.root / "installed.json"
        self._lock = threading.Lock()
        self._kokoro_pipeline = None

    @property
    def installed(self) -> bool:
        return self.kokoro_installed and self.studio_installed

    @property
    def kokoro_installed(self) -> bool:
        required = (
            self.marker,
            self.kokoro_root / "config.json",
            self.kokoro_root / "kokoro-v1_0.pth",
            self.kokoro_root / "voices" / "pf_dora.pt",
            self.kokoro_root / "voices" / "pm_alex.pt",
            self.kokoro_root / "voices" / "pm_santa.pt",
        )
        return all(path.exists() for path in required)

    @property
    def studio_installed(self) -> bool:
        required = (
            self.marker,
            self.model_root / "ve.pt",
            self.model_root / "conds.pt",
            self.model_root / "t3_pt_br.safetensors",
            self.model_root / "s3gen_v3.pt",
            self.model_root / "grapheme_mtl_merged_expanded_v1.json",
            self.prompt_root / "pt_br_f2.wav",
        )
        return all(path.exists() for path in required)

    @staticmethod
    def _ollama_url(path: str) -> str:
        root = os.environ.get("NEXO_OLLAMA_ROOT", "http://127.0.0.1:11434").rstrip("/")
        return root + path

    def _release_ollama(self) -> list[str]:
        released: list[str] = []
        try:
            with urllib.request.urlopen(self._ollama_url("/api/ps"), timeout=3) as response:
                payload = json.loads(response.read(1_000_000).decode("utf-8"))
            for item in payload.get("models", []):
                model = str(item.get("name") or item.get("model") or "").strip()
                if not model:
                    continue
                request = urllib.request.Request(
                    self._ollama_url("/api/generate"),
                    data=json.dumps({"model": model, "keep_alive": 0}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=15) as response:
                    response.read(100_000)
                released.append(model)
            if released:
                LOG.info("VRAM released_from_ollama=%s", ",".join(released))
        except Exception as exc:
            LOG.warning("Não foi possível solicitar liberação preventiva do Ollama: %s", exc)
        return released

    def _load_studio_model(self):
        if not self.studio_installed:
            raise RuntimeError("A voz Neural Estúdio ainda não foi instalada pelo painel ADM")
        if str(self.source_root) not in sys.path:
            sys.path.insert(0, str(self.source_root))
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA não está disponível para a voz neural")
        from chatterbox.src.chatterbox.tts import ChatterboxTTS
        return ChatterboxTTS.from_local(str(self.model_root), "cuda")

    def _load_kokoro(self):
        if not self.kokoro_installed:
            raise RuntimeError("As vozes neurais rápidas ainda não foram instaladas pelo painel ADM")
        if self._kokoro_pipeline is None:
            import torch
            from kokoro import KModel, KPipeline

            torch.set_num_threads(max(1, min(6, os.cpu_count() or 4)))
            model = KModel(
                repo_id="hexgrad/Kokoro-82M",
                config=str(self.kokoro_root / "config.json"),
                model=str(self.kokoro_root / "kokoro-v1_0.pth"),
            ).to("cpu").eval()
            self._kokoro_pipeline = KPipeline(
                lang_code="p",
                repo_id="hexgrad/Kokoro-82M",
                model=model,
                device="cpu",
            )
            LOG.info("Kokoro pt-BR carregado em CPU para baixa latência")
        return self._kokoro_pipeline

    def warm_low_latency_voice(self) -> None:
        if not self.kokoro_installed:
            return
        with self._lock:
            try:
                self._load_kokoro()
            except Exception:
                LOG.exception("Falha no aquecimento da voz neural rápida")

    @staticmethod
    def _wav_bytes(samples, sample_rate: int) -> bytes:
        import numpy as np
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0.99:
            audio = audio / peak * 0.98
        fade = min(audio.size // 2, max(1, int(sample_rate * 0.008)))
        if fade > 1:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            audio[:fade] *= ramp
            audio[-fade:] *= ramp[::-1]
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(sample_rate))
            wav.writeframes(pcm.tobytes())
        return output.getvalue()

    def synthesize(self, text: str, voice: str) -> tuple[bytes, dict[str, Any]]:
        phrase = _clean_for_speech(text)
        if not phrase:
            raise ValueError("O texto para voz está vazio")
        profile = PROFILES.get(voice)
        if not profile:
            raise ValueError("Perfil de voz neural desconhecido")
        if not self._lock.acquire(timeout=30):
            raise RuntimeError("A voz neural já está sintetizando outra resposta")
        started = time.monotonic()
        model = None
        try:
            import numpy as np
            generated = []
            released: list[str] = []
            sample_rate = 24_000
            chunks = _chunks(phrase)
            if profile["engine"] == "kokoro":
                pipeline = self._load_kokoro()
                voice_path = self.kokoro_root / "voices" / profile["voice"]
                for index, chunk in enumerate(chunks):
                    results = pipeline(
                        chunk,
                        voice=str(voice_path),
                        speed=float(profile["speed"]),
                    )
                    chunk_segments = []
                    for result in results:
                        if result.audio is not None:
                            chunk_segments.append(result.audio.detach().cpu().numpy())
                    if chunk_segments:
                        generated.append(
                            _join_audio_segments(chunk_segments, sample_rate)
                        )
                    if index + 1 < len(chunks):
                        generated.append(np.zeros(int(sample_rate * 0.12), dtype=np.float32))
            else:
                released = self._release_ollama()
                import torch
                gc.collect()
                torch.cuda.empty_cache()
                model = self._load_studio_model()
                torch.manual_seed(int(profile["seed"]))
                torch.cuda.manual_seed_all(int(profile["seed"]))
                sample_rate = int(model.sr)
                for index, chunk in enumerate(chunks):
                    wav = model.generate(
                        chunk,
                        audio_prompt_path=str(self.prompt_root / profile["prompt"]),
                        exaggeration=float(profile["exaggeration"]),
                        temperature=float(profile["temperature"]),
                        cfg_weight=float(profile["cfg_weight"]),
                        language_id="pt",
                    )
                    generated.append(wav.squeeze(0).detach().cpu().numpy())
                    if index + 1 < len(chunks):
                        generated.append(np.zeros(int(sample_rate * 0.14), dtype=np.float32))
            samples = np.concatenate(generated) if generated else np.zeros(1, dtype=np.float32)
            data = self._wav_bytes(samples, sample_rate)
            elapsed = time.monotonic() - started
            return data, {
                "voice": voice,
                "label": profile["label"],
                "engine": profile["engine"],
                "seconds": round(elapsed, 2),
                "audio_seconds": round(float(samples.size) / sample_rate, 2),
                "chunks": len(chunks),
                "ollama_released": released,
            }
        finally:
            if model is not None:
                del model
                gc.collect()
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                except Exception:
                    pass
            self._lock.release()


class NeuralTTSHandler(BaseHTTPRequestHandler):
    server_version = "NexoNeuralTTS/1.0"

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/health":
            self._json(200, {
                "ok": True,
                "installed": self.server.runtime.installed,
                "kokoro_installed": self.server.runtime.kokoro_installed,
                "studio_installed": self.server.runtime.studio_installed,
                "profiles": list(PROFILES),
                "model_loaded": self.server.runtime._kokoro_pipeline is not None,
            })
            return
        if parsed.path not in {"/v2/synthesize", "/synthesize"}:
            self._json(404, {"ok": False, "error": "Rota inexistente"})
            return
        params = urllib.parse.parse_qs(parsed.query)
        utterance = str((params.get("utterance") or [""])[0])
        voice = str((params.get("voice") or ["neural:ptbr-natural"])[0])
        try:
            data, meta = self.server.runtime.synthesize(utterance, voice)
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:
            LOG.exception("Falha na síntese neural")
            self._json(503, {"ok": False, "error": str(exc)})
            return
        LOG.info(
            "TTS synthesis completed voice=%s engine=%s chunks=%s "
            "synth_seconds=%s audio_seconds=%s bytes=%d",
            meta.get("voice"),
            meta.get("engine"),
            meta.get("chunks"),
            meta.get("seconds"),
            meta.get("audio_seconds"),
            len(data),
        )
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Nexo-Voice", meta["voice"])
        self.send_header("X-Nexo-Seconds", str(meta["seconds"]))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        # The default formatter includes the full query string (the spoken
        # text). Keep conversation content out of service logs.
        LOG.info("HTTP request completed status=%s", args[1] if len(args) > 1 else "unknown")


class NeuralTTSServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address, runtime: NeuralRuntime):
        super().__init__(address, NeuralTTSHandler)
        self.runtime = runtime


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexo neural pt-BR TTS service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    server = NeuralTTSServer((args.host, args.port), NeuralRuntime(Path(args.root)))
    LOG.info("Neural TTS listening on http://%s:%d installed=%s", args.host, args.port, server.runtime.installed)
    threading.Thread(
        target=server.runtime.warm_low_latency_voice,
        name="nexo-neural-warmup",
        daemon=True,
    ).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
