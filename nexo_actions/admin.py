"""Secure local administration for Nexo's voice and AI runtime."""

from __future__ import annotations

import base64
import array
import copy
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import DEFAULT_CONFIG_PATH, load_config
from .jobs import JobManager
from .secrets import load_openai_api_key, openai_credential_status
from .usage import UsageLedger
from .stt_quality import (
    SMART_LISTENER_SETTINGS,
    active_stt_settings,
    configure_smart_stt,
)


LOG = logging.getLogger("nexo.admin")
DEFAULT_OVOS_CONFIG_PATH = Path.home() / ".config" / "mycroft" / "mycroft.conf"
DEFAULT_AUTH_PATH = Path.home() / ".config" / "nexo" / "admin-auth.json"
DEFAULT_VOICE_ROOT = Path.home() / ".local" / "share" / "jarvis-voices"
DEFAULT_NEURAL_TTS_ROOT = Path.home() / ".local" / "share" / "nexo" / "neural-tts"
DEFAULT_LISTENING_PROMPT_ROOT = Path.home() / ".local" / "share" / "nexo" / "listening-prompts"
SESSION_TTL_SECONDS = 20 * 60
PIPER_CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json"
PIPER_DOWNLOAD_ROOT = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
_VOICE_CATALOG_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_VOICE_CATALOG_LOCK = threading.RLock()
LISTENING_PROMPT_VERSION = "openai-voice-v3"
OPENAI_MODELS_ENDPOINT = "https://api.openai.com/v1/models"
OPENAI_SPEECH_ENDPOINT = "https://api.openai.com/v1/audio/speech"

OPENAI_AGENT_MODELS = (
    {
        "id": "gpt-5.6-sol",
        "label": "GPT-5.6 Sol",
        "profile": "Agente multimodal principal",
        "capabilities": ["tools", "vision", "reasoning"],
        "recommended": True,
    },
    {
        "id": "gpt-5.6-terra",
        "label": "GPT-5.6 Terra",
        "profile": "Equilíbrio entre inteligência, latência e custo",
        "capabilities": ["tools", "vision", "reasoning"],
    },
    {
        "id": "gpt-5.6-luna",
        "label": "GPT-5.6 Luna",
        "profile": "Mais econômico para tarefas cotidianas",
        "capabilities": ["tools", "vision", "reasoning"],
    },
)
OPENAI_REALTIME_MODELS = (
    {
        "id": "gpt-realtime-2.1",
        "label": "GPT Realtime 2.1",
        "profile": "Conversa de voz em tempo real",
        "capabilities": ["audio", "realtime", "tools"],
        "recommended": True,
    },
    {
        "id": "gpt-realtime-2.1-mini",
        "label": "GPT Realtime 2.1 Mini",
        "profile": "Voz rápida e de menor custo",
        "capabilities": ["audio", "realtime", "tools"],
    },
)
OPENAI_VOICES = (
    {"id": "marin", "label": "Marin", "profile": "Natural e expressiva"},
    {"id": "cedar", "label": "Cedar", "profile": "Natural e encorpada"},
    {"id": "coral", "label": "Coral", "profile": "Clara e acolhedora"},
    {"id": "verse", "label": "Verse", "profile": "Viva e conversacional"},
    {"id": "sage", "label": "Sage", "profile": "Calma e segura"},
    {"id": "ash", "label": "Ash", "profile": "Direta e equilibrada"},
    {"id": "ballad", "label": "Ballad", "profile": "Suave e expressiva"},
    {"id": "echo", "label": "Echo", "profile": "Grave e presente"},
    {"id": "shimmer", "label": "Shimmer", "profile": "Leve e luminosa"},
    {"id": "alloy", "label": "Alloy", "profile": "Neutra e versátil"},
)
REASONING_EFFORTS = (
    {"id": "low", "label": "Baixo", "profile": "Menor latência"},
    {"id": "medium", "label": "Médio", "profile": "Maior confiabilidade em ações"},
    {"id": "high", "label": "Alto", "profile": "Mais análise para tarefas complexas"},
)
TEXT_VERBOSITIES = (
    {
        "id": "low",
        "label": "Conciso",
        "profile": "Resultado direto por padrão",
        "recommended": True,
    },
    {"id": "medium", "label": "Equilibrado", "profile": "Mais contexto espontâneo"},
    {"id": "high", "label": "Detalhado", "profile": "Respostas extensas por padrão"},
)
ROUTINE_REASONING_EFFORTS = (
    {
        "id": "none",
        "label": "Sem raciocínio extra",
        "profile": "Menor latência para ações cotidianas",
        "recommended": True,
    },
    {"id": "low", "label": "Baixo", "profile": "Mais análise para tarefas rápidas"},
    {"id": "medium", "label": "Médio", "profile": "Prioriza confiabilidade"},
    {"id": "high", "label": "Alto", "profile": "Maior latência"},
)
REALTIME_REASONING_EFFORTS = (
    {
        "id": "minimal",
        "label": "Mínimo",
        "profile": "Resposta de voz mais imediata",
        "recommended": True,
    },
    {"id": "low", "label": "Baixo", "profile": "Rápido com um pouco mais de análise"},
    {"id": "medium", "label": "Médio", "profile": "Mais análise antes de falar"},
    {"id": "high", "label": "Alto", "profile": "Maior latência para casos excepcionais"},
)
REALTIME_RESPONSE_STYLES = (
    {
        "id": "concise",
        "label": "Conciso",
        "profile": "Age primeiro e responde em uma frase",
        "recommended": True,
    },
    {
        "id": "balanced",
        "label": "Equilibrado",
        "profile": "Permite duas ou três frases quando ajudam",
    },
)

STT_MODELS = (
    {"id": "tiny", "label": "Tiny", "profile": "Ultrarrápido", "vram_gb": 1},
    {"id": "base", "label": "Base", "profile": "Rápido", "vram_gb": 1},
    {"id": "small", "label": "Small", "profile": "Equilibrado", "vram_gb": 2},
    {"id": "medium", "label": "Medium", "profile": "Alta precisão", "vram_gb": 5},
    {"id": "large-v3", "label": "Large v3", "profile": "Máxima precisão", "vram_gb": 10},
    {"id": "turbo", "label": "Turbo", "profile": "Rápido e preciso", "vram_gb": 6},
)

NEURAL_VOICE_PROFILES = (
    {
        "id": "neural:ptbr-natural", "label": "Alex Neural BR · Natural",
        "quality": "neural", "profile": "Natural, masculina e de baixa latência (CPU)",
        "downloadable": True,
    },
    {
        "id": "neural:ptbr-calm", "label": "Dora Neural BR · Serena",
        "quality": "neural", "profile": "Natural, feminina e de baixa latência (CPU)",
        "downloadable": False,
    },
    {
        "id": "neural:ptbr-expressive", "label": "Orion Neural BR · Expressiva",
        "quality": "neural", "profile": "Masculina, grave e de baixa latência (CPU)",
        "downloadable": False,
    },
    {
        "id": "neural:ptbr-studio", "label": "Aurora Neural BR · Estúdio",
        "quality": "neural", "profile": "Máxima naturalidade; usa a GPU sequencialmente",
        "downloadable": False,
    },
)


def _read_json(path: Path, default: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return copy.deepcopy(dict(default or {}))
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Configuração inválida em {path}")
    return value


def _atomic_write_json(path: Path, value: Mapping[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _atomic_write_bytes(path: Path, value: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _trim_listening_prompt(
    payload: bytes,
    *,
    leading_padding_ms: int = 35,
    trailing_padding_ms: int = 45,
    fade_ms: int = 6,
) -> bytes:
    """Remove inaudible TTS padding while keeping a click-free spoken cue."""
    source_buffer = io.BytesIO(payload)
    try:
        with wave.open(source_buffer, "rb") as source:
            channels = source.getnchannels()
            width = source.getsampwidth()
            rate = source.getframerate()
            frames = source.getnframes()
            compression = (source.getcomptype(), source.getcompname())
            pcm = source.readframes(frames)
    except (EOFError, wave.Error):
        return payload
    if channels not in {1, 2} or width != 2 or rate < 8000 or not pcm:
        return payload

    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    frame_count = len(samples) // channels
    if frame_count < 1:
        return payload
    levels = [
        max(abs(value) for value in samples[index * channels : (index + 1) * channels])
        for index in range(frame_count)
    ]
    peak = max(levels)
    if peak < 32:
        return payload
    threshold = max(24, int(peak * 0.002))
    active = [index for index, level in enumerate(levels) if level > threshold]
    if not active:
        return payload

    start = max(0, active[0] - int(rate * leading_padding_ms / 1000))
    end = min(frame_count, active[-1] + 1 + int(rate * trailing_padding_ms / 1000))
    # Do not rewrite a file when trimming would be negligible.
    if start <= int(rate * 0.005) and frame_count - end <= int(rate * 0.005):
        return payload
    cropped = array.array("h", samples[start * channels : end * channels])
    fade_frames = min(int(rate * fade_ms / 1000), len(cropped) // channels // 2)
    for frame in range(fade_frames):
        gain = frame / max(1, fade_frames - 1)
        opposite = len(cropped) // channels - 1 - frame
        for channel in range(channels):
            head = frame * channels + channel
            tail = opposite * channels + channel
            cropped[head] = int(cropped[head] * gain)
            cropped[tail] = int(cropped[tail] * gain)
    if sys.byteorder != "little":
        cropped.byteswap()

    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(width)
        target.setframerate(rate)
        target.setcomptype(*compression)
        target.writeframes(cropped.tobytes())
    return output.getvalue()


class AdminAuth:
    """Scrypt password storage and short-lived in-memory browser sessions."""

    def __init__(
        self,
        path: Path = DEFAULT_AUTH_PATH,
        *,
        session_ttl: int = SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path).expanduser()
        self.session_ttl = max(60, int(session_ttl))
        self.clock = clock
        self._sessions: dict[str, dict[str, Any]] = {}
        self._failures: list[float] = []
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        if not self.path.exists():
            return False
        try:
            data = _read_json(self.path)
            return bool(data.get("salt") and data.get("hash"))
        except Exception:
            LOG.exception("Não foi possível ler a autenticação ADM")
            # A damaged credential file must fail closed, never reopen setup.
            return True

    @staticmethod
    def _validate_password(password: str) -> str:
        value = str(password)
        if len(value) < 8:
            raise ValueError("Use uma senha com pelo menos 8 caracteres")
        if len(value) > 128:
            raise ValueError("A senha pode ter no máximo 128 caracteres")
        return value

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
        )

    def setup(self, password: str) -> dict[str, str]:
        value = self._validate_password(password)
        with self._lock:
            if self.configured:
                raise PermissionError("A senha administrativa já foi configurada")
            if self.path.exists():
                raise PermissionError("A autenticação ADM existe, mas precisa ser reparada localmente")
            salt = secrets.token_bytes(16)
            digest = self._derive(value, salt)
            _atomic_write_json(self.path, {
                "version": 1,
                "algorithm": "scrypt-n16384-r8-p1",
                "salt": base64.b64encode(salt).decode("ascii"),
                "hash": base64.b64encode(digest).decode("ascii"),
                "created_at": int(self.clock()),
            })
            return self._new_session_locked()

    def login(self, password: str) -> dict[str, str]:
        now = self.clock()
        with self._lock:
            self._failures = [stamp for stamp in self._failures if now - stamp < 60]
            if len(self._failures) >= 5:
                remaining = max(1, int(60 - (now - self._failures[0])))
                raise PermissionError(f"Muitas tentativas. Tente novamente em {remaining} segundos")
            data = _read_json(self.path)
            if not data.get("salt") or not data.get("hash"):
                raise PermissionError("A senha administrativa ainda não foi criada")
            try:
                salt = base64.b64decode(data["salt"], validate=True)
                expected = base64.b64decode(data["hash"], validate=True)
                actual = self._derive(str(password), salt)
            except Exception as exc:
                raise PermissionError("A configuração de autenticação é inválida") from exc
            if not hmac.compare_digest(actual, expected):
                self._failures.append(now)
                raise PermissionError("Senha incorreta")
            self._failures.clear()
            return self._new_session_locked()

    def _new_session_locked(self) -> dict[str, str]:
        self._cleanup_locked()
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = self.clock()
        self._sessions[token] = {"csrf": csrf, "expires": now + self.session_ttl}
        return {"token": token, "csrf": csrf}

    def validate(self, token: str | None, csrf: str | None = None, *, write: bool = False) -> bool:
        if not token:
            return False
        now = self.clock()
        with self._lock:
            self._cleanup_locked()
            session = self._sessions.get(token)
            if not session or session["expires"] <= now:
                return False
            if write and (not csrf or not hmac.compare_digest(str(csrf), session["csrf"])):
                return False
            session["expires"] = now + self.session_ttl
            return True

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def csrf(self, token: str) -> str | None:
        with self._lock:
            session = self._sessions.get(token)
            return str(session["csrf"]) if session else None

    def _cleanup_locked(self) -> None:
        now = self.clock()
        expired = [token for token, data in self._sessions.items() if data["expires"] <= now]
        for token in expired:
            self._sessions.pop(token, None)


class AdminService:
    """Validated configuration catalog, preview, apply, health check and rollback."""

    def __init__(
        self,
        *,
        config_path: Path = DEFAULT_CONFIG_PATH,
        ovos_config_path: Path = DEFAULT_OVOS_CONFIG_PATH,
        auth_path: Path | None = None,
        voice_root: Path = DEFAULT_VOICE_ROOT,
        neural_tts_root: Path = DEFAULT_NEURAL_TTS_ROOT,
        listening_prompt_root: Path = DEFAULT_LISTENING_PROMPT_ROOT,
        command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        reload_callback: Callable[[], None] | None = None,
        prompt_renderer: Callable[[str, str, float], bytes] | None = None,
        openai_prompt_renderer: Callable[[str, str], bytes] | None = None,
        openai_key_loader: Callable[[], str | None] = load_openai_api_key,
        credential_status_loader: Callable[[], Mapping[str, Any]] = openai_credential_status,
        usage_ledger: UsageLedger | None = None,
    ) -> None:
        self.config_path = Path(config_path).expanduser()
        self.ovos_config_path = Path(ovos_config_path).expanduser()
        self.voice_root = Path(voice_root).expanduser()
        self.neural_tts_root = Path(neural_tts_root).expanduser()
        self.listening_prompt_root = Path(listening_prompt_root).expanduser()
        self.project_root = Path(__file__).resolve().parents[1]
        self.command_runner = command_runner
        self.reload_callback = reload_callback
        self.prompt_renderer = prompt_renderer
        self.openai_prompt_renderer = openai_prompt_renderer
        self.openai_key_loader = openai_key_loader
        self.credential_status_loader = credential_status_loader
        self.usage_ledger = usage_ledger or UsageLedger()
        resolved_auth = auth_path or os.environ.get("NEXO_ADMIN_AUTH_PATH") or DEFAULT_AUTH_PATH
        self.auth = AdminAuth(Path(resolved_auth).expanduser())
        self.jobs = JobManager(max_workers=1, retention_seconds=900)
        self._apply_lock = threading.Lock()

    def close(self) -> None:
        self.jobs.shutdown(wait=False)

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "configured": self.auth.configured,
            "secure_local": True,
            "session_minutes": self.auth.session_ttl // 60,
        }

    def _safe_credential_status(self) -> dict[str, Any]:
        """Normalize credential metadata without ever touching its value."""
        try:
            raw = dict(self.credential_status_loader())
        except Exception:
            LOG.exception("Não foi possível consultar o estado da credencial OpenAI")
            raw = {}
        return {
            "configured": bool(raw.get("configured")),
            "source": str(raw.get("source") or "none"),
            "secure_storage": bool(raw.get("secure_storage")),
        }

    def test_provider(self, requested_provider: str) -> dict[str, Any]:
        """Perform a bounded, read-only connectivity check for the ADM panel."""
        provider = str(requested_provider or self.settings()["ai"]["provider"]).casefold()
        if provider not in {"openai", "ollama"}:
            raise ValueError("Provedor de inteligência inválido")
        started = time.monotonic()
        if provider == "ollama":
            models = self._ollama_models()
            return {
                "ok": bool(models),
                "provider": "ollama",
                "status": "ready" if models else "unavailable",
                "models_visible": len(models),
                "latency_ms": round((time.monotonic() - started) * 1000),
            }

        credential = self._safe_credential_status()
        key = self.openai_key_loader()
        if not key:
            return {
                "ok": False,
                "provider": "openai",
                "status": "credential_missing",
                "credential": credential,
            }
        request = urllib.request.Request(
            OPENAI_MODELS_ENDPOINT,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                "User-Agent": "Nexo-Admin/2.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read(2_000_000).decode("utf-8"))
            visible = {
                str(item.get("id") or "")
                for item in payload.get("data", [])
                if isinstance(item, Mapping)
            }
            configured_model = self.settings()["ai"]["profiles"]["openai"]["agent_model"]
            return {
                "ok": True,
                "provider": "openai",
                "status": "authorized",
                "credential": credential,
                "models_visible": len(visible),
                "configured_model_visible": configured_model in visible,
                "latency_ms": round((time.monotonic() - started) * 1000),
            }
        except urllib.error.HTTPError as exc:
            status = {
                401: "invalid_credential",
                403: "access_denied",
                429: "rate_limited",
            }.get(exc.code, "http_error")
            return {
                "ok": False,
                "provider": "openai",
                "status": status,
                "http_status": int(exc.code),
                "credential": credential,
                "latency_ms": round((time.monotonic() - started) * 1000),
            }
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return {
                "ok": False,
                "provider": "openai",
                "status": "connection_error",
                "error_type": type(exc).__name__,
                "credential": credential,
                "latency_ms": round((time.monotonic() - started) * 1000),
            }

    def catalog(self) -> dict[str, Any]:
        voices = self._voice_catalog(include_remote=True)
        ollama_models = self._ollama_models()
        credential = self._safe_credential_status()
        published_qualities = sorted({
            voice["quality"] for voice in voices
            if voice.get("official") and voice.get("quality")
        })
        return {
            "voices": voices,
            "voice_qualities": published_qualities,
            "stt_models": list(STT_MODELS),
            # Retained for older ADM clients during the schema migration.
            "ai_models": ollama_models,
            "ai": {
                "providers": [
                    {
                        "id": "openai",
                        "label": "OpenAI",
                        "profile": "Nuvem multimodal em tempo real",
                        "available": bool(credential.get("configured")),
                    },
                    {
                        "id": "ollama",
                        "label": "Local (Ollama)",
                        "profile": "Compatibilidade local",
                        "available": bool(ollama_models),
                    },
                ],
                "agent_models": {
                    "openai": list(OPENAI_AGENT_MODELS),
                    "ollama": ollama_models,
                },
                "realtime_models": {"openai": list(OPENAI_REALTIME_MODELS)},
                "voices": {"openai": list(OPENAI_VOICES)},
                "reasoning_efforts": list(REASONING_EFFORTS),
                "text_verbosities": list(TEXT_VERBOSITIES),
                "routine_reasoning_efforts": list(ROUTINE_REASONING_EFFORTS),
                "realtime_reasoning_efforts": list(REALTIME_REASONING_EFFORTS),
                "realtime_response_styles": list(REALTIME_RESPONSE_STYLES),
                "credential": credential,
            },
            "usage": self.usage_ledger.snapshot(),
            "services": self._service_states(),
        }

    def settings(self) -> dict[str, Any]:
        # load_config performs the legacy -> default deep merge without writing
        # anything, so installations created before provider switching remain
        # usable immediately.
        raw_nexo = _read_json(self.config_path)
        nexo = load_config(self.config_path, force=True)
        ovos = _read_json(self.ovos_config_path)
        tts_root = ovos.get("tts", {})
        tts_module = str(tts_root.get("module") or "ovos-tts-plugin-phoonnx")
        hybrid_tts = tts_root.get("nexo-hybrid-tts", {})
        piper_tts = tts_root.get("ovos-tts-plugin-phoonnx", {})
        is_neural = tts_module == "nexo-hybrid-tts"
        stt = active_stt_settings(ovos)
        listener = ovos.get("listener", {})
        conversation = nexo.get("conversation", {})
        audio = nexo.get("audio", {})
        ai = nexo.get("ai", {})
        profiles = ai.get("profiles", {})
        openai_profile = profiles.get("openai", {})
        legacy_ollama = nexo.get("ollama", {})
        if isinstance(raw_nexo.get("ai"), Mapping):
            ollama_profile = {**legacy_ollama, **profiles.get("ollama", {})}
        else:
            # On a legacy file, the old root block is the user's authoritative
            # local selection; copy it into the provider profile on first save.
            ollama_profile = {**profiles.get("ollama", {}), **legacy_ollama}
        return {
            "tts": {
                "engine": "neural" if is_neural else "piper",
                "model": str((hybrid_tts.get("voice") if is_neural else piper_tts.get("model")) or ""),
                "model_config": str(piper_tts.get("model_config") or hybrid_tts.get("fallback_config") or ""),
                "length_scale": float(
                    hybrid_tts.get("fallback_length_scale", 0.85)
                    if is_neural else piper_tts.get("length-scale", 1.0)
                ),
            },
            "stt": {
                "model": str(stt.get("model", "medium")),
                "beam_size": int(stt.get("beam_size", 5)),
                "use_cuda": bool(stt.get("use_cuda", True)),
                "compute_type": str(stt.get("compute_type", "float16")),
            },
            "ai": {
                "provider": str(ai.get("provider", "openai")),
                "fallback_provider": str(ai.get("fallback_provider", "ollama")),
                "fallback_enabled": bool(ai.get("fallback_enabled", False)),
                "profiles": {
                    "openai": {
                        "agent_model": str(openai_profile.get("agent_model", "gpt-5.6-sol")),
                        "realtime_model": str(openai_profile.get("realtime_model", "gpt-realtime-2.1")),
                        "voice": str(openai_profile.get("voice", "marin")),
                        "reasoning_effort": str(openai_profile.get("reasoning_effort", "medium")),
                        "agent_text_verbosity": str(
                            openai_profile.get("agent_text_verbosity", "low")
                        ),
                        "routine_reasoning_effort": str(
                            openai_profile.get("routine_reasoning_effort", "none")
                        ),
                        "routine_agent_model": str(
                            openai_profile.get("routine_agent_model", "gpt-5.6-terra")
                        ),
                        "realtime_reasoning_effort": str(
                            openai_profile.get("realtime_reasoning_effort", "minimal")
                        ),
                        "realtime_max_output_tokens": int(
                            openai_profile.get("realtime_max_output_tokens", 1024)
                        ),
                        "realtime_response_style": str(
                            openai_profile.get("realtime_response_style", "concise")
                        ),
                    },
                    "ollama": {
                        "url": str(ollama_profile.get("url", "http://localhost:11434/api/chat")),
                        "model": str(ollama_profile.get("model", "gemma4:12b")),
                        "keep_alive": str(ollama_profile.get("keep_alive", "30m")),
                    },
                },
            },
            "conversation": {
                "timeout_seconds": int(conversation.get("timeout_seconds", 180)),
                "max_messages": int(conversation.get("max_messages", 24)),
                "max_tokens": int(conversation.get("max_tokens", 2200)),
                "barge_in": bool(conversation.get("barge_in", True)),
                "barge_in_requires_wakeword": bool(
                    conversation.get("barge_in_requires_wakeword", True)
                ),
                "echo_guard": bool(conversation.get("echo_guard", True)),
                "echo_window_seconds": float(conversation.get("echo_window_seconds", 8)),
                "barge_in_volume_percent": int(
                    listener.get("barge_in_volume", conversation.get("barge_in_volume_percent", 100))
                ),
            },
            "audio": {
                "input_gain_percent": int(audio.get("input_gain_percent", 135)),
                "auto_recover": bool(audio.get("auto_recover", True)),
            },
        }

    def submit_apply(self, requested: Mapping[str, Any]):
        normalized = self._validate_settings(requested)
        return self.jobs.submit(
            "admin_apply",
            self._apply,
            normalized,
            timeout=90,
            dedupe_key="admin_apply",
            pass_context=True,
        )

    def audio_health(self) -> dict[str, Any]:
        process = self._audio_command("check", "--no-probe", timeout=8)
        lines = [line.strip() for line in (process.stdout or "").splitlines() if line.strip()]
        if not lines:
            raise RuntimeError((process.stderr or "Diagnóstico do microfone não retornou dados").strip())
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("Diagnóstico do microfone retornou dados inválidos") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Diagnóstico do microfone retornou formato inválido")
        return payload

    def submit_repair_audio(self):
        return self.jobs.submit(
            "admin_repair_audio",
            self._repair_audio,
            timeout=120,
            dedupe_key="admin_repair_audio",
            pass_context=True,
        )

    def _audio_command(self, *arguments: str, timeout: float = 90):
        return self.command_runner(
            [sys.executable, "-m", "nexo_actions.audio_recovery", *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(self.project_root),
        )

    def _repair_audio(self, *, job_context) -> dict[str, Any]:
        job_context.report_progress(8, "Verificando a cadeia do microfone")
        job_context.raise_if_cancelled()
        process = self._audio_command("repair", "--reason", "painel-adm", timeout=105)
        if process.returncode:
            lines = [line.strip() for line in (process.stdout or "").splitlines() if line.strip()]
            detail = process.stderr.strip() if process.stderr else ""
            if lines:
                try:
                    detail = json.loads(lines[-1]).get("error") or detail
                except (AttributeError, json.JSONDecodeError):
                    detail = lines[-1]
            raise RuntimeError(detail or "Não foi possível reaplicar o microfone")
        job_context.report_progress(92, "Confirmando amostras no listener")
        lines = [line.strip() for line in (process.stdout or "").splitlines() if line.strip()]
        payload = json.loads(lines[-1]) if lines else {"ok": True, "healthy": True}
        job_context.report_progress(100, "Microfone reaplicado e monitorado")
        return payload

    def submit_install_voice(self, voice_id: str):
        voice = next((
            item for item in self._voice_catalog(include_remote=True)
            if item["id"] == str(voice_id) and item.get("official") and not item.get("installed")
        ), None)
        if not voice or not voice.get("downloadable", True):
            raise ValueError("Escolha uma voz oficial do catálogo pt-BR")
        if voice.get("engine") == "neural":
            return self.jobs.submit(
                "admin_install_neural_voice",
                self._install_neural_voice,
                voice,
                timeout=2400,
                dedupe_key="voice:chatterbox-v3-pt-br",
                pass_context=True,
            )
        return self.jobs.submit(
            "admin_install_voice",
            self._install_voice,
            voice,
            timeout=240,
            dedupe_key=f"voice:{voice['id']}",
            pass_context=True,
        )

    def preview_tts(self, text: str, model: str, length_scale: float) -> bytes:
        phrase = " ".join(str(text).split())[:240]
        if not phrase:
            raise ValueError("Digite uma frase para testar a voz")
        voice = self._resolve_voice(model)
        if voice.get("engine") == "neural":
            params = urllib.parse.urlencode({
                "utterance": phrase, "lang": "pt-BR", "voice": voice["id"],
            })
            request = urllib.request.Request(
                "http://127.0.0.1:8791/v2/synthesize?" + params,
                headers={"Accept": "audio/wav", "User-Agent": "Nexo-Admin/1.0"},
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    audio = response.read(64 * 1024 * 1024 + 1)
            except Exception as exc:
                raise RuntimeError(
                    "A voz neural não respondeu; o Nexo em produção usaria o fallback Piper"
                ) from exc
            if len(audio) > 64 * 1024 * 1024 or not audio.startswith(b"RIFF"):
                raise RuntimeError("A voz neural devolveu uma amostra inválida")
            return audio
        scale = self._bounded_float(length_scale, 0.55, 1.8, "Velocidade da voz")
        try:
            from phoonnx.config import SynthesisConfig
            from phoonnx.voice import TTSVoice
        except ImportError as exc:
            raise RuntimeError("O sintetizador PhonNnx não está disponível neste serviço") from exc

        loaded = TTSVoice.load(str(voice["model"]), str(voice["config"]))
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            loaded.synthesize_wav(phrase, wav, SynthesisConfig(length_scale=scale))
        return output.getvalue()

    def _voice_catalog(self, *, include_remote: bool = False) -> list[dict[str, Any]]:
        voices = []
        if self.voice_root.exists():
            for model in sorted(self.voice_root.rglob("*.onnx")):
                config = Path(str(model) + ".json")
                if not config.is_file():
                    continue
                stem = model.stem
                quality = next((q for q in ("low", "medium", "high") if stem.endswith("-" + q)), "custom")
                voices.append({
                    "id": str(model),
                    "label": stem.replace("pt_BR-", "").replace("_pt-BR", "").replace("-", " ").title(),
                    "quality": quality,
                    "model": str(model),
                    "config": str(config),
                    "installed": True,
                    "official": False,
                    "engine": "piper",
                })
        if include_remote:
            installed_names = {Path(item["model"]).name: item for item in voices}
            for remote in self._remote_voice_catalog():
                installed = installed_names.get(remote["filename"])
                if installed:
                    installed["official"] = True
                    installed["catalog_id"] = remote["id"]
                    installed["size"] = remote["size"]
                else:
                    voices.append(remote)
        neural_installed = (self.neural_tts_root / "installed.json").is_file()
        for profile in NEURAL_VOICE_PROFILES:
            voices.append({
                **profile,
                "model": profile["id"],
                "config": "",
                "engine": "neural",
                "installed": neural_installed,
                "official": True,
                "size": 3_600_000_000,
            })
        return voices

    def _remote_voice_catalog(self) -> list[dict[str, Any]]:
        global _VOICE_CATALOG_CACHE
        now = time.time()
        with _VOICE_CATALOG_LOCK:
            if _VOICE_CATALOG_CACHE and now - _VOICE_CATALOG_CACHE[0] < 21_600:
                return copy.deepcopy(_VOICE_CATALOG_CACHE[1])
        try:
            request = urllib.request.Request(PIPER_CATALOG_URL, headers={"User-Agent": "Nexo-Admin/1.0"})
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read(2_000_000).decode("utf-8"))
            voices = []
            for key, item in payload.items():
                if item.get("language", {}).get("code") != "pt_BR":
                    continue
                files = item.get("files", {})
                model_path = next((name for name in files if name.endswith(".onnx")), None)
                config_path = next((name for name in files if name.endswith(".onnx.json")), None)
                if not model_path or not config_path:
                    continue
                filename = Path(model_path).name
                if not re.fullmatch(r"pt_BR-[A-Za-z0-9_]+-(?:x_low|low|medium|high)\.onnx", filename):
                    continue
                voices.append({
                    "id": str(key),
                    "catalog_id": str(key),
                    "label": str(item.get("name") or key).replace("_", " ").title(),
                    "quality": str(item.get("quality") or "custom"),
                    "installed": False,
                    "official": True,
                    "engine": "piper",
                    "downloadable": True,
                    "filename": filename,
                    "model_url": PIPER_DOWNLOAD_ROOT + urllib.parse.quote(model_path),
                    "config_url": PIPER_DOWNLOAD_ROOT + urllib.parse.quote(config_path),
                    "model_md5": str(files[model_path].get("md5_digest") or ""),
                    "config_md5": str(files[config_path].get("md5_digest") or ""),
                    "size": int(files[model_path].get("size_bytes") or 0),
                })
            with _VOICE_CATALOG_LOCK:
                _VOICE_CATALOG_CACHE = (now, copy.deepcopy(voices))
            return voices
        except Exception as exc:
            LOG.warning("Catálogo oficial de vozes indisponível: %s", exc)
            return []

    def _resolve_voice(self, requested: str) -> dict[str, Any]:
        value = str(requested).strip()
        for voice in self._voice_catalog():
            if voice.get("installed") and (
                hmac.compare_digest(value, voice["id"])
                or hmac.compare_digest(value, voice["model"])
            ):
                return voice
        raise ValueError("Escolha uma voz instalada pelo catálogo do Nexo")

    def _install_neural_voice(self, voice: dict[str, Any], *, job_context) -> dict[str, Any]:
        if (self.neural_tts_root / "installed.json").is_file():
            return {"ok": True, "already_installed": True, "model": voice["id"]}
        command = [
            sys.executable,
            "-m",
            "nexo_actions.neural_tts_install",
            "--root",
            str(self.neural_tts_root),
            "--project-root",
            str(self.project_root),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        output: list[str] = []
        try:
            for line in process.stdout or []:
                line = line.rstrip()
                output.append(line)
                match = re.match(r"^PROGRESS\s+(\d+)\s+(.+)$", line)
                if match:
                    job_context.report_progress(int(match.group(1)), match.group(2))
                job_context.raise_if_cancelled()
            return_code = process.wait(timeout=30)
            if return_code:
                raise RuntimeError("\n".join(output[-30:]) or "Instalação neural falhou")
        except Exception:
            if process.poll() is None:
                process.terminate()
            raise

        unit_source = self.project_root / "systemd" / "nexo-neural-tts.service"
        unit_target = Path.home() / ".config" / "systemd" / "user" / unit_source.name
        unit_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(unit_source, unit_target)
        for service_command in (
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "nexo-neural-tts.service"],
        ):
            result = self.command_runner(
                service_command, capture_output=True, text=True, timeout=30
            )
            if result.returncode:
                raise RuntimeError(
                    (result.stderr or result.stdout or "Falha ao iniciar a voz neural").strip()
                )
        self._wait_healthy("nexo-neural-tts.service")
        job_context.report_progress(100, "Voz Neural pt-BR instalada e ativa")
        return {"ok": True, "model": voice["id"], "engine": "neural"}

    def _install_voice(self, voice: dict[str, Any], *, job_context) -> dict[str, Any]:
        folder_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", voice["id"])
        destination = self.voice_root / folder_name
        model_path = destination / voice["filename"]
        config_path = Path(str(model_path) + ".json")
        if model_path.is_file() and config_path.is_file():
            return {"ok": True, "already_installed": True, "model": str(model_path)}
        destination.mkdir(parents=True, exist_ok=True)
        model_temp = Path(str(model_path) + ".part")
        config_temp = Path(str(config_path) + ".part")
        try:
            job_context.report_progress(5, f"Baixando a voz {voice['label']}")
            self._download_catalog_file(
                voice["model_url"], model_temp, voice["model_md5"],
                maximum=150_000_000, job_context=job_context, progress_start=5, progress_end=88,
            )
            job_context.raise_if_cancelled()
            job_context.report_progress(90, "Validando configuração da voz")
            self._download_catalog_file(
                voice["config_url"], config_temp, voice["config_md5"],
                maximum=1_000_000, job_context=job_context, progress_start=90, progress_end=97,
            )
            with config_temp.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict) or not loaded:
                raise ValueError("Configuração da voz inválida")
            os.replace(model_temp, model_path)
            os.replace(config_temp, config_path)
            job_context.report_progress(100, "Voz instalada e pronta para teste")
            return {"ok": True, "model": str(model_path), "config": str(config_path)}
        finally:
            for temporary in (model_temp, config_temp):
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _download_catalog_file(
        url: str,
        destination: Path,
        expected_md5: str,
        *,
        maximum: int,
        job_context,
        progress_start: float,
        progress_end: float,
    ) -> None:
        if not url.startswith(PIPER_DOWNLOAD_ROOT):
            raise ValueError("Origem da voz não autorizada")
        request = urllib.request.Request(url, headers={"User-Agent": "Nexo-Admin/1.0"})
        digest = hashlib.md5(usedforsecurity=False)
        downloaded = 0
        with urllib.request.urlopen(request, timeout=20) as response, destination.open("wb") as output:
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > maximum:
                raise ValueError("Arquivo de voz excede o limite seguro")
            while True:
                job_context.raise_if_cancelled()
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > maximum:
                    raise ValueError("Arquivo de voz excede o limite seguro")
                output.write(chunk)
                digest.update(chunk)
                if declared:
                    ratio = min(1.0, downloaded / declared)
                    job_context.report_progress(
                        progress_start + (progress_end - progress_start) * ratio,
                        "Baixando modelo oficial",
                    )
        if not downloaded:
            raise RuntimeError("O catálogo retornou um arquivo vazio")
        if expected_md5 and not hmac.compare_digest(digest.hexdigest(), expected_md5.casefold()):
            raise RuntimeError("A assinatura da voz baixada não confere")

    def _ollama_models(self) -> list[dict[str, Any]]:
        config = load_config(self.config_path, force=True)
        endpoint = str(config.get("ollama", {}).get("url", "http://localhost:11434/api/chat"))
        tags_url = endpoint.rsplit("/", 1)[0] + "/tags"
        try:
            request = urllib.request.Request(tags_url, headers={"User-Agent": "Nexo-Admin/1.0"})
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.loads(response.read(1_000_000).decode("utf-8"))
            return [
                {
                    "id": str(item.get("name") or item.get("model")),
                    "size": int(item.get("size") or 0),
                    "modified_at": item.get("modified_at"),
                }
                for item in payload.get("models", [])
                if item.get("name") or item.get("model")
            ]
        except Exception as exc:
            LOG.warning("Catálogo Ollama indisponível: %s", exc)
            return []

    def _service_states(self) -> list[dict[str, str]]:
        result = []
        for service in (
            "ovos-core.service", "ovos-audio.service", "ovos-listener.service",
            "nexo-ui.service", "nexo-neural-tts.service", "nexo-pulse-aec.service",
            "nexo-audio-watchdog.service",
        ):
            try:
                process = self.command_runner(
                    ["systemctl", "--user", "is-active", service],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                state = (process.stdout or "unknown").strip()
            except Exception:
                state = "unknown"
            result.append({"name": service.removesuffix(".service"), "state": state})
        return result

    def _validate_settings(self, requested: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(requested, Mapping):
            raise ValueError("Configurações inválidas")
        current = self.settings()
        output = copy.deepcopy(current)

        tts = requested.get("tts", {})
        if tts:
            voice = self._resolve_voice(str(tts.get("model", current["tts"]["model"])))
            output["tts"] = {
                "engine": voice.get("engine", "piper"),
                "id": voice["id"],
                "model": voice["model"],
                "model_config": voice["config"],
                "length_scale": self._bounded_float(
                    tts.get("length_scale", current["tts"]["length_scale"]), 0.55, 1.8, "Velocidade da voz"
                ),
            }

        stt = requested.get("stt", {})
        if stt:
            model = str(stt.get("model", current["stt"]["model"]))
            if model not in {item["id"] for item in STT_MODELS}:
                raise ValueError("Modelo de reconhecimento de voz não permitido")
            compute_type = str(stt.get("compute_type", current["stt"]["compute_type"]))
            if compute_type not in {"float16", "int8_float16", "int8", "float32"}:
                raise ValueError("Precisão STT inválida")
            output["stt"] = {
                "model": model,
                "beam_size": self._bounded_int(stt.get("beam_size", 5), 1, 10, "Beam size"),
                "use_cuda": bool(stt.get("use_cuda", True)),
                "compute_type": compute_type,
            }

        ai = requested.get("ai", {})
        if ai:
            current_ai = current["ai"]
            provider = str(ai.get("provider", current_ai["provider"])).strip().casefold()
            if provider not in {"openai", "ollama"}:
                raise ValueError("Provedor de inteligência inválido")
            fallback_provider = str(
                ai.get("fallback_provider", current_ai["fallback_provider"])
            ).strip().casefold()
            if fallback_provider != "ollama":
                raise ValueError("O fallback compatível do Nexo deve ser o Ollama local")
            fallback_enabled = bool(ai.get("fallback_enabled", current_ai["fallback_enabled"]))
            requested_profiles = ai.get("profiles", {})
            if requested_profiles and not isinstance(requested_profiles, Mapping):
                raise ValueError("Perfis de inteligência inválidos")

            openai_current = current_ai["profiles"]["openai"]
            openai_requested = requested_profiles.get("openai", {})
            agent_model = str(
                openai_requested.get("agent_model", openai_current["agent_model"])
            ).strip()
            realtime_model = str(
                openai_requested.get("realtime_model", openai_current["realtime_model"])
            ).strip()
            voice = str(openai_requested.get("voice", openai_current["voice"])).strip()
            reasoning_effort = str(
                openai_requested.get("reasoning_effort", openai_current["reasoning_effort"])
            ).strip().casefold()
            agent_text_verbosity = str(
                openai_requested.get(
                    "agent_text_verbosity",
                    openai_current.get("agent_text_verbosity", "low"),
                )
            ).strip().casefold()
            routine_reasoning_effort = str(
                openai_requested.get(
                    "routine_reasoning_effort",
                    openai_current.get("routine_reasoning_effort", "none"),
                )
            ).strip().casefold()
            routine_agent_model = str(
                openai_requested.get(
                    "routine_agent_model",
                    openai_current.get("routine_agent_model", "gpt-5.6-terra"),
                )
            ).strip()
            realtime_reasoning_effort = str(
                openai_requested.get(
                    "realtime_reasoning_effort",
                    openai_current.get("realtime_reasoning_effort", "minimal"),
                )
            ).strip().casefold()
            realtime_max_output_tokens = self._bounded_int(
                openai_requested.get(
                    "realtime_max_output_tokens",
                    openai_current.get("realtime_max_output_tokens", 1024),
                ),
                64,
                4096,
                "Limite da resposta Realtime",
            )
            realtime_response_style = str(
                openai_requested.get(
                    "realtime_response_style",
                    openai_current.get("realtime_response_style", "concise"),
                )
            ).strip().casefold()
            if agent_model not in {item["id"] for item in OPENAI_AGENT_MODELS}:
                raise ValueError("Escolha um modelo OpenAI compatível com ações e visão")
            if realtime_model not in {item["id"] for item in OPENAI_REALTIME_MODELS}:
                raise ValueError("Escolha um modelo OpenAI Realtime permitido")
            if voice not in {item["id"] for item in OPENAI_VOICES}:
                raise ValueError("Escolha uma voz OpenAI permitida")
            if reasoning_effort not in {item["id"] for item in REASONING_EFFORTS}:
                raise ValueError("Esforço de raciocínio do agente é inválido")
            if agent_text_verbosity not in {
                item["id"] for item in TEXT_VERBOSITIES
            }:
                raise ValueError("Detalhamento textual do agente é inválido")
            if routine_reasoning_effort not in {
                item["id"] for item in ROUTINE_REASONING_EFFORTS
            }:
                raise ValueError("Esforço das tarefas rápidas é inválido")
            if routine_agent_model and routine_agent_model not in {
                item["id"] for item in OPENAI_AGENT_MODELS
            }:
                raise ValueError("Modelo das tarefas rápidas é inválido")
            if realtime_reasoning_effort not in {
                item["id"] for item in REALTIME_REASONING_EFFORTS
            }:
                raise ValueError("Esforço de raciocínio Realtime é inválido")
            if realtime_response_style not in {
                item["id"] for item in REALTIME_RESPONSE_STYLES
            }:
                raise ValueError("Estilo de resposta Realtime é inválido")

            ollama_current = current_ai["profiles"]["ollama"]
            ollama_requested = requested_profiles.get("ollama", {})
            model = str(ollama_requested.get("model", ollama_current["model"])).strip()
            installed = {item["id"] for item in self._ollama_models()}
            if not model:
                raise ValueError("Escolha um modelo Ollama local")
            if installed and model not in installed:
                raise ValueError("Escolha um modelo Ollama instalado")
            if (
                not installed
                and model != ollama_current["model"]
                and (provider == "ollama" or fallback_enabled)
            ):
                raise ValueError("O Ollama está indisponível; mantenha o modelo local atual")
            keep_alive = str(
                ollama_requested.get("keep_alive", ollama_current["keep_alive"])
            ).strip()
            if not keep_alive or len(keep_alive) > 16 or not keep_alive.replace("-", "").replace("_", "").isalnum():
                raise ValueError("Tempo de permanência do modelo inválido")
            output["ai"] = {
                "provider": provider,
                "fallback_provider": fallback_provider,
                "fallback_enabled": fallback_enabled,
                "profiles": {
                    "openai": {
                        "agent_model": agent_model,
                        "realtime_model": realtime_model,
                        "voice": voice,
                        "reasoning_effort": reasoning_effort,
                        "agent_text_verbosity": agent_text_verbosity,
                        "routine_reasoning_effort": routine_reasoning_effort,
                        "routine_agent_model": routine_agent_model,
                        "realtime_reasoning_effort": realtime_reasoning_effort,
                        "realtime_max_output_tokens": realtime_max_output_tokens,
                        "realtime_response_style": realtime_response_style,
                    },
                    "ollama": {
                        "url": str(ollama_current.get("url", "http://localhost:11434/api/chat")),
                        "model": model,
                        "keep_alive": keep_alive,
                    },
                },
            }

        conversation = requested.get("conversation", {})
        if conversation:
            output["conversation"] = {
                "timeout_seconds": self._bounded_int(conversation.get("timeout_seconds", 180), 30, 1800, "Tempo da conversa"),
                "max_messages": self._bounded_int(conversation.get("max_messages", 24), 4, 80, "Memória de mensagens"),
                "max_tokens": self._bounded_int(conversation.get("max_tokens", 2200), 256, 8192, "Tokens"),
                "barge_in": bool(conversation.get("barge_in", True)),
                "barge_in_requires_wakeword": bool(
                    conversation.get("barge_in_requires_wakeword", True)
                ),
                "echo_guard": bool(conversation.get("echo_guard", True)),
                "echo_window_seconds": self._bounded_float(conversation.get("echo_window_seconds", 8), 2, 20, "Janela anti-eco"),
                "barge_in_volume_percent": self._bounded_int(conversation.get("barge_in_volume_percent", 100), 0, 100, "Volume durante interrupção"),
            }
        audio = requested.get("audio", {})
        if audio:
            output["audio"] = {
                "input_gain_percent": self._bounded_int(
                    audio.get("input_gain_percent", current["audio"]["input_gain_percent"]),
                    50,
                    200,
                    "Ganho do microfone",
                ),
                "auto_recover": bool(audio.get("auto_recover", True)),
            }
        return output

    @staticmethod
    def _bounded_int(value: Any, minimum: int, maximum: int, label: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} inválido") from exc
        if parsed < minimum or parsed > maximum:
            raise ValueError(f"{label} deve ficar entre {minimum} e {maximum}")
        return parsed

    @staticmethod
    def _bounded_float(value: Any, minimum: float, maximum: float, label: str) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} inválido") from exc
        if parsed < minimum or parsed > maximum:
            raise ValueError(f"{label} deve ficar entre {minimum:g} e {maximum:g}")
        return parsed

    def _apply(self, normalized: dict[str, Any], *, job_context) -> dict[str, Any]:
        if not self._apply_lock.acquire(blocking=False):
            raise RuntimeError("Outra alteração administrativa está em andamento")
        old_nexo = _read_json(self.config_path, load_config())
        old_ovos = _read_json(self.ovos_config_path)
        restarted: list[str] = []
        try:
            job_context.report_progress(8, "Validando configuração")
            new_nexo = copy.deepcopy(old_nexo)
            new_ovos = copy.deepcopy(old_ovos)

            new_nexo["ai"] = copy.deepcopy(normalized["ai"])
            # Legacy mirror: components that have not yet migrated to the
            # provider factory continue to see the selected local profile.
            new_nexo.setdefault("ollama", {}).update(
                copy.deepcopy(normalized["ai"]["profiles"]["ollama"])
            )
            new_nexo.setdefault("conversation", {}).update(normalized["conversation"])
            new_nexo.setdefault("audio", {}).update(normalized["audio"])
            tts_root = new_ovos.setdefault("tts", {})
            piper = tts_root.setdefault("ovos-tts-plugin-phoonnx", {})
            if normalized["tts"]["engine"] == "neural":
                fallback_model = str(
                    piper.get("model")
                    or tts_root.get("nexo-hybrid-tts", {}).get("fallback_model")
                    or ""
                )
                fallback_config = str(
                    piper.get("model_config")
                    or tts_root.get("nexo-hybrid-tts", {}).get("fallback_config")
                    or ""
                )
                if not Path(fallback_model).is_file() or not Path(fallback_config).is_file():
                    raise RuntimeError("Mantenha ao menos uma voz Piper instalada como fallback")
                tts_root["module"] = "nexo-hybrid-tts"
                tts_root.setdefault("nexo-hybrid-tts", {}).update({
                    "voice": normalized["tts"]["id"],
                    "endpoint": "http://127.0.0.1:8791/v2/synthesize",
                    "request_timeout": 120,
                    "fallback_model": fallback_model,
                    "fallback_config": fallback_config,
                    "fallback_length_scale": normalized["tts"]["length_scale"],
                })
            else:
                if "module" in tts_root:
                    tts_root["module"] = "ovos-tts-plugin-phoonnx"
                piper.update({
                    "model": normalized["tts"]["model"],
                    "model_config": normalized["tts"]["model_config"],
                    "length-scale": normalized["tts"]["length_scale"],
                })
            openai_voice = normalized["ai"]["profiles"]["openai"]["voice"]
            use_openai_prompt = normalized["ai"]["provider"] == "openai"
            prompt_key = "|".join((
                LISTENING_PROMPT_VERSION,
                "openai" if use_openai_prompt else normalized["tts"]["engine"],
                openai_voice if use_openai_prompt else normalized["tts"]["id"],
                "gpt-4o-mini-tts" if use_openai_prompt else f"{normalized['tts']['length_scale']:.3f}",
            ))
            prompt_name = "manda-" + hashlib.sha256(prompt_key.encode("utf-8")).hexdigest()[:16] + ".wav"
            prompt_path = self.listening_prompt_root / prompt_name
            if not prompt_path.is_file():
                job_context.report_progress(18, "Gerando a resposta de escuta com a voz escolhida")
                job_context.raise_if_cancelled()
                if use_openai_prompt:
                    prompt_audio = _trim_listening_prompt(
                        self._render_openai_listening_prompt("Manda.", openai_voice)
                    )
                else:
                    renderer = self.prompt_renderer or self.preview_tts
                    prompt_audio = _trim_listening_prompt(
                        renderer(
                            "Manda.",
                            normalized["tts"]["id"],
                            normalized["tts"]["length_scale"],
                        )
                    )
                try:
                    with wave.open(io.BytesIO(prompt_audio), "rb") as audio:
                        if audio.getnchannels() not in {1, 2} or audio.getnframes() < 1:
                            raise RuntimeError("A resposta de escuta gerada está vazia")
                except (EOFError, wave.Error) as exc:
                    raise RuntimeError("A voz devolveu uma resposta de escuta inválida") from exc
                _atomic_write_bytes(prompt_path, prompt_audio)
            new_ovos.setdefault("hotwords", {}).setdefault("nexo", {})["sound"] = str(prompt_path)
            stt_root = new_ovos.setdefault("stt", {})
            configure_smart_stt(stt_root, normalized["stt"])
            listener = new_ovos.setdefault("listener", {})
            listener.update(SMART_LISTENER_SETTINGS)
            listener["fake_barge_in"] = bool(
                normalized["conversation"]["barge_in"]
                and not normalized["conversation"]["barge_in_requires_wakeword"]
            )
            listener["barge_in_volume"] = normalized["conversation"]["barge_in_volume_percent"]

            changed_nexo = new_nexo != old_nexo
            changed_audio = new_nexo.get("audio") != old_nexo.get("audio")
            changed_tts = new_ovos.get("tts") != old_ovos.get("tts")
            changed_listener = (
                new_ovos.get("stt") != old_ovos.get("stt")
                or new_ovos.get("listener") != old_ovos.get("listener")
                or new_ovos.get("hotwords") != old_ovos.get("hotwords")
            )
            job_context.report_progress(22, "Salvando cópia atômica")
            job_context.raise_if_cancelled()
            _atomic_write_json(self.config_path, new_nexo)
            _atomic_write_json(self.ovos_config_path, new_ovos)

            if changed_audio:
                job_context.raise_if_cancelled()
                job_context.report_progress(34, "Aplicando ganho e monitoramento do microfone")
                self._restart("nexo-audio-watchdog.service")
                restarted.append("nexo-audio-watchdog.service")
                gain = self._audio_command(
                    "apply-gain",
                    str(normalized["audio"]["input_gain_percent"]),
                    timeout=12,
                )
                if gain.returncode:
                    raise RuntimeError(
                        (gain.stderr or gain.stdout or "Falha ao aplicar ganho do microfone").strip()
                    )

            if changed_tts:
                job_context.raise_if_cancelled()
                job_context.report_progress(42, "Recarregando a voz")
                self._restart("ovos-audio.service")
                restarted.append("ovos-audio.service")
            if changed_listener:
                job_context.raise_if_cancelled()
                job_context.report_progress(62, "Recarregando a escuta")
                self._restart("ovos-listener.service")
                restarted.append("ovos-listener.service")
            if changed_nexo and self.reload_callback:
                job_context.report_progress(76, "Recarregando a inteligência")
                self.reload_callback()

            job_context.report_progress(88, "Verificando a saúde do Nexo")
            for service in restarted:
                self._wait_healthy(service)
            job_context.report_progress(100, "Configuração aplicada")
            return {
                "ok": True,
                "restarted": restarted,
                "reloaded_skill": bool(changed_nexo),
                "listening_prompt": str(prompt_path),
            }
        except Exception:
            LOG.exception("Falha ao aplicar configuração ADM; restaurando estado anterior")
            _atomic_write_json(self.config_path, old_nexo)
            _atomic_write_json(self.ovos_config_path, old_ovos)
            for service in restarted:
                try:
                    self._restart(service)
                except Exception:
                    LOG.exception("Falha ao reiniciar %s durante rollback", service)
            if self.reload_callback:
                try:
                    self.reload_callback()
                except Exception:
                    LOG.exception("Falha ao recarregar skill durante rollback")
            raise
        finally:
            self._apply_lock.release()

    def _render_openai_listening_prompt(self, text: str, voice: str) -> bytes:
        """Render the wake acknowledgement with the selected Realtime voice."""
        if self.openai_prompt_renderer is not None:
            return self.openai_prompt_renderer(str(text), str(voice))
        key = self.openai_key_loader()
        if not key:
            raise RuntimeError(
                "Configure a OPENAI_API_KEY antes de usar uma voz OpenAI no aviso de escuta"
            )
        payload = {
            "model": "gpt-4o-mini-tts",
            "voice": str(voice),
            "input": str(text),
            "instructions": (
                "Fale em português brasileiro. Diga apenas a palavra fornecida, "
                "com tom caloroso, confiante e muito breve, sem introdução."
            ),
            "response_format": "wav",
        }
        request = urllib.request.Request(
            OPENAI_SPEECH_ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "audio/wav",
                "User-Agent": "Nexo-Admin/2.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                audio = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"A OpenAI recusou a geração do aviso de escuta (HTTP {int(exc.code)})"
            ) from None
        except (OSError, TimeoutError) as exc:
            raise RuntimeError(
                "A OpenAI não respondeu ao gerar o aviso de escuta"
            ) from exc
        if len(audio) > 8 * 1024 * 1024 or not audio.startswith(b"RIFF"):
            raise RuntimeError("A OpenAI devolveu um aviso de escuta inválido")
        try:
            with wave.open(io.BytesIO(audio), "rb") as stream:
                if stream.getnframes() < 1 or stream.getsampwidth() != 2:
                    raise RuntimeError("A OpenAI devolveu um WAV sem áudio PCM válido")
        except wave.Error as exc:
            raise RuntimeError("A OpenAI devolveu um WAV inválido") from exc
        return audio

    def _restart(self, service: str) -> None:
        if service not in {
            "ovos-audio.service", "ovos-listener.service", "nexo-neural-tts.service",
            "nexo-audio-watchdog.service",
        }:
            raise ValueError("Serviço não autorizado")
        process = self.command_runner(
            ["systemctl", "--user", "restart", service],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if process.returncode:
            raise RuntimeError((process.stderr or process.stdout or f"Falha ao reiniciar {service}").strip())

    def _wait_healthy(self, service: str) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            process = self.command_runner(
                ["systemctl", "--user", "is-active", service],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if process.returncode == 0 and process.stdout.strip() == "active":
                return
            time.sleep(0.5)
        raise RuntimeError(f"{service.removesuffix('.service')} não voltou saudável")
