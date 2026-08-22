"""Loopback-only HTTP/SSE server for the Nexo holographic interface."""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import urlparse

from ..admin import AdminService


LOG = logging.getLogger("nexo.ui")
ALLOWED_STATES = {
    "idle", "listening", "thinking", "speaking", "acting",
    "confirming", "error", "offline",
}
TEXT_FIELDS = {"label", "detail", "text", "tool"}


class StateHub:
    """Thread-safe, versioned UI state shared by HTTP and messagebus threads."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._version = 1
        self._state: dict[str, Any] = {
            "state": "idle",
            "label": "Disponível",
            "detail": "Diga Nexo para começar",
            "text": "",
            "tool": "",
            "progress": None,
            "sources": [],
            "connected": False,
            "voice_active": False,
            "updated_at": time.time(),
        }

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        requested_state = str(values.get("state") or "").casefold()
        with self._condition:
            clean: dict[str, Any] = {}
            if requested_state:
                normalized_state = requested_state if requested_state in ALLOWED_STATES else "error"
                if normalized_state != self._state["state"]:
                    clean.update({
                        "label": "", "detail": "", "text": "", "tool": "",
                        "progress": None, "sources": [],
                    })
                clean["state"] = normalized_state
            for key in TEXT_FIELDS:
                if key in values:
                    clean[key] = str(values.get(key) or "")[:600]
            if "progress" in values:
                progress = values.get("progress")
                clean["progress"] = None if progress is None else max(0.0, min(100.0, float(progress)))
            if "connected" in values:
                clean["connected"] = bool(values["connected"])
            if "voice_active" in values:
                clean["voice_active"] = bool(values["voice_active"])
            if "sources" in values:
                sources = values.get("sources") or []
                if not isinstance(sources, list):
                    sources = []
                clean["sources"] = [
                    {
                        "title": str(item.get("title") or item.get("domain") or "Fonte")[:120],
                        "url": str(item.get("url") or "")[:1000],
                        "domain": str(item.get("domain") or "")[:120],
                    }
                    for item in sources[:6]
                    if isinstance(item, dict) and str(item.get("url") or "").startswith(("http://", "https://"))
                ]
            clean["updated_at"] = time.time()
            self._state.update(clean)
            self._version += 1
            self._condition.notify_all()
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {"version": self._version, **self._state}

    def wait_for_change(self, version: int, timeout: float = 15.0) -> dict[str, Any] | None:
        with self._condition:
            if self._version == version:
                self._condition.wait(timeout=max(0.05, timeout))
            if self._version == version:
                return None
            return {"version": self._version, **self._state}


class NexoUIBridge:
    """Mirrors OVOS events into the UI and accepts local typed utterances."""

    def __init__(self, hub: StateHub, client=None) -> None:
        self.hub = hub
        self.client = client

    def start(self) -> None:
        if self.client is None:
            from ovos_bus_client.client import MessageBusClient

            self.client = MessageBusClient()
        self.client.on("open", self._on_open)
        self.client.on("close", self._on_close)
        self.client.on("error", self._on_error)
        self.client.on("nexo.ui.state", self._on_state)
        self.client.on("recognizer_loop:record_begin", self._on_listening)
        self.client.on("recognizer_loop:record_end", self._on_processing)
        self.client.on("recognizer_loop:wakeword", self._on_listening)
        self.client.on("recognizer_loop:audio_output_start", self._on_speaking)
        self.client.on("recognizer_loop:audio_output_end", self._on_audio_end)
        self.client.run_in_thread()

    def stop(self) -> None:
        if self.client is not None:
            self.client.close()

    def emit_utterance(self, text: str) -> None:
        value = " ".join(str(text).split())[:1200]
        if not value:
            raise ValueError("Mensagem vazia")
        if self.client is None:
            raise RuntimeError("Nexo ainda não está conectado ao barramento de voz")
        from ovos_bus_client.message import Message

        self.client.emit(Message(
            "recognizer_loop:utterance",
            {"utterances": [value], "lang": "pt-BR"},
            {"source": "nexo-ui"},
        ))
        self.hub.update({"state": "thinking", "label": "Processando", "text": value})

    def reload_skill(self) -> None:
        """Ask OVOS to reload only Nexo after a local configuration change."""
        if self.client is None:
            raise RuntimeError("Barramento de voz indisponível")
        from ovos_bus_client.message import Message

        self.client.emit(Message(
            "mycroft.skills.reload",
            {"skill_id": "nexo-actions.samtec"},
            {"source": "nexo-admin"},
        ))

    def _on_open(self, _message=None) -> None:
        self.hub.update({"connected": True})

    def _on_close(self, _message=None) -> None:
        self.hub.update({"connected": False, "state": "offline", "label": "Desconectado"})

    def _on_error(self, _message=None) -> None:
        self.hub.update({"connected": False})

    def _on_state(self, message) -> None:
        data = dict(getattr(message, "data", {}) or {})
        if "state" in data and "voice_active" not in data:
            data["voice_active"] = str(data.get("state") or "") == "speaking"
        self.hub.update(data)

    def _on_listening(self, _message=None) -> None:
        if self.hub.snapshot()["state"] in {"acting", "confirming", "speaking"}:
            return
        self.hub.update({"state": "listening", "label": "Ouvindo", "detail": "Pode falar"})

    def _on_processing(self, _message=None) -> None:
        if self.hub.snapshot()["state"] in {"acting", "confirming", "speaking"}:
            return
        self.hub.update({
            "state": "thinking",
            "label": "Processando",
            "detail": "Entendendo seu pedido",
        })

    def _on_speaking(self, _message=None) -> None:
        # The skill is the authority for the logical state. Raw audio events
        # only drive animation, otherwise a short prompt can erase an active
        # task and make the interface lie that Nexo is available.
        self.hub.update({"voice_active": True})

    def _on_audio_end(self, _message=None) -> None:
        self.hub.update({"voice_active": False})


def _handler_factory(
    hub: StateHub,
    bridge: NexoUIBridge | None,
    admin: AdminService,
):
    static_root = resources.files("nexo_actions.ui").joinpath("static")
    mime_types = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}

    class UIHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "NexoUI/1.0"

        def log_message(self, fmt, *args):
            LOG.debug("HTTP %s", fmt % args)

        def _headers(
            self,
            status: int,
            content_type: str,
            length: int | None = None,
            extra: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; media-src 'self' blob:; frame-ancestors 'none'",
            )
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def _json(
            self,
            status: int,
            payload: dict[str, Any],
            *,
            extra: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(body), extra)
            self.wfile.write(body)

        def _request_json(self, maximum: int = 32_768) -> dict[str, Any]:
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ValueError("Envie conteúdo JSON")
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > maximum:
                raise ValueError("Tamanho de requisição inválido")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("O corpo deve ser um objeto JSON")
            return payload

        def _session_token(self) -> str | None:
            raw = self.headers.get("Cookie", "")
            try:
                cookie = SimpleCookie(raw)
                morsel = cookie.get("nexo_admin")
                return morsel.value if morsel else None
            except Exception:
                return None

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return False
            parsed = urlparse(origin)
            host = self.headers.get("Host", "")
            return parsed.scheme == "http" and parsed.netloc == host

        def _authorized(self, *, write: bool = False) -> bool:
            token = self._session_token()
            csrf = self.headers.get("X-Nexo-CSRF") if write else None
            if write and not self._same_origin():
                self._json(403, {"ok": False, "error": "Origem da requisição rejeitada"})
                return False
            if not admin.auth.validate(token, csrf, write=write):
                self._json(401, {"ok": False, "error": "Sessão administrativa inválida ou expirada"})
                return False
            return True

        @staticmethod
        def _session_headers(token: str) -> dict[str, str]:
            return {
                "Set-Cookie": (
                    f"nexo_admin={token}; Path=/; HttpOnly; SameSite=Strict; "
                    f"Max-Age={admin.auth.session_ttl}"
                )
            }

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                self._json(200, hub.snapshot())
                return
            if path == "/api/admin/status":
                token = self._session_token()
                payload = admin.status()
                payload["authenticated"] = admin.auth.validate(token)
                if payload["authenticated"]:
                    payload["csrf"] = admin.auth.csrf(token or "")
                self._json(200, payload)
                return
            if path in {"/api/admin/settings", "/api/admin/catalog"}:
                if not self._authorized():
                    return
                try:
                    payload = admin.settings() if path.endswith("settings") else admin.catalog()
                    self._json(200, {"ok": True, **payload})
                except Exception as exc:
                    LOG.exception("Falha no painel ADM")
                    self._json(500, {"ok": False, "error": str(exc)})
                return
            if path == "/api/admin/audio-health":
                if not self._authorized():
                    return
                try:
                    self._json(200, admin.audio_health())
                except Exception as exc:
                    LOG.exception("Falha no diagnóstico do microfone")
                    self._json(500, {"ok": False, "healthy": False, "error": str(exc)})
                return
            if path.startswith("/api/admin/jobs/"):
                if not self._authorized():
                    return
                job = admin.jobs.get(path.rsplit("/", 1)[-1])
                if not job:
                    self._json(404, {"ok": False, "error": "Tarefa não encontrada"})
                else:
                    self._json(200, {"ok": True, "job": job})
                return
            if path == "/events":
                self._events()
                return
            if path == "/":
                path = "/index.html"
            elif path in {"/admin", "/admin/"}:
                path = "/admin.html"
            name = path.lstrip("/")
            if "/" in name or "\\" in name or name.startswith("."):
                self._json(404, {"ok": False, "error": "not found"})
                return
            resource = static_root.joinpath(name)
            try:
                body = resource.read_bytes()
            except (FileNotFoundError, IsADirectoryError):
                self._json(404, {"ok": False, "error": "not found"})
                return
            suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
            self._headers(200, mime_types.get(suffix, "application/octet-stream"), len(body))
            self.wfile.write(body)

        def _events(self) -> None:
            self._headers(200, "text/event-stream; charset=utf-8")
            version = 0
            try:
                while True:
                    state = hub.wait_for_change(version, 12)
                    if state is None:
                        self.wfile.write(b": pulse\n\n")
                    else:
                        version = state["version"]
                        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self):
            path = urlparse(self.path).path
            if path.startswith("/api/admin/"):
                self._admin_post(path)
                return
            if path != "/api/utterance":
                self._json(404, {"ok": False, "error": "not found"})
                return
            if self.headers.get("X-Nexo-UI") != "local" or not self.headers.get("Content-Type", "").startswith("application/json"):
                self._json(403, {"ok": False, "error": "request rejected"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4096:
                    raise ValueError("Tamanho inválido")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if bridge is None:
                    raise RuntimeError("Barramento de voz indisponível")
                bridge.emit_utterance(payload.get("text", ""))
            except Exception as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            self._json(202, {"ok": True})

        def _admin_post(self, path: str) -> None:
            if not self._same_origin():
                self._json(403, {"ok": False, "error": "Origem da requisição rejeitada"})
                return
            try:
                if path == "/api/admin/setup":
                    payload = self._request_json(4096)
                    session = admin.auth.setup(payload.get("password", ""))
                    self._json(
                        201,
                        {"ok": True, "csrf": session["csrf"]},
                        extra=self._session_headers(session["token"]),
                    )
                    return
                if path == "/api/admin/login":
                    payload = self._request_json(4096)
                    session = admin.auth.login(payload.get("password", ""))
                    self._json(
                        200,
                        {"ok": True, "csrf": session["csrf"]},
                        extra=self._session_headers(session["token"]),
                    )
                    return
                if not self._authorized(write=True):
                    return
                if path == "/api/admin/logout":
                    admin.auth.logout(self._session_token())
                    self._json(200, {"ok": True}, extra={
                        "Set-Cookie": "nexo_admin=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
                    })
                    return
                if path == "/api/admin/test-provider":
                    payload = self._request_json(4096)
                    self._json(200, admin.test_provider(payload.get("provider", "")))
                    return
                if path == "/api/admin/apply":
                    payload = self._request_json()
                    submission = admin.submit_apply(payload.get("settings", payload))
                    self._json(202, {
                        "ok": True,
                        "job_id": submission.job_id,
                        "created": submission.created,
                        "state": submission.state,
                    })
                    return
                if path == "/api/admin/install-voice":
                    payload = self._request_json(4096)
                    submission = admin.submit_install_voice(payload.get("voice_id", ""))
                    self._json(202, {
                        "ok": True,
                        "job_id": submission.job_id,
                        "created": submission.created,
                        "state": submission.state,
                    })
                    return
                if path == "/api/admin/repair-audio":
                    submission = admin.submit_repair_audio()
                    self._json(202, {
                        "ok": True,
                        "job_id": submission.job_id,
                        "created": submission.created,
                        "state": submission.state,
                    })
                    return
                if path == "/api/admin/preview-tts":
                    payload = self._request_json(8192)
                    audio = admin.preview_tts(
                        payload.get("text", ""),
                        payload.get("model", ""),
                        payload.get("length_scale", 1.0),
                    )
                    self._headers(200, "audio/wav", len(audio))
                    self.wfile.write(audio)
                    return
                self._json(404, {"ok": False, "error": "not found"})
            except PermissionError as exc:
                self._json(403, {"ok": False, "error": str(exc)})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                LOG.exception("Falha na operação ADM")
                self._json(500, {"ok": False, "error": str(exc)})

    return UIHandler


def create_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    connect_bus: bool = True,
    admin_service: AdminService | None = None,
):
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("A interface do Nexo deve permanecer vinculada ao loopback")
    hub = StateHub()
    bridge = NexoUIBridge(hub) if connect_bus else None
    admin = admin_service or AdminService(reload_callback=(bridge.reload_skill if bridge else None))
    server = ThreadingHTTPServer((host, int(port)), _handler_factory(hub, bridge, admin))
    server.daemon_threads = True
    server.nexo_hub = hub
    server.nexo_bridge = bridge
    server.nexo_admin = admin
    return server


def run_server(host: str = "127.0.0.1", port: int = 8787, *, connect_bus: bool = True) -> None:
    server = create_server(host, port, connect_bus=connect_bus)
    bridge = server.nexo_bridge
    if bridge:
        bridge.start()
    LOG.info("Nexo UI disponível em http://%s:%s", host, server.server_port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.nexo_admin.close()
        if bridge:
            bridge.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Interface holográfica local do Nexo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-bus", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_server(args.host, args.port, connect_bus=not args.no_bus)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
