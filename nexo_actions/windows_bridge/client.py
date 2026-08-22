"""Authenticated JSON-RPC client used by Nexo inside WSL."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from ..config import expand_windows_env, load_config, windows_to_wsl


class BridgeError(RuntimeError):
    pass


class BridgeUnavailable(BridgeError):
    pass


class WindowsBridgeClient:
    def __init__(self, url: str | None = None, token: str | None = None, timeout: float | None = None):
        config = load_config().get("bridge", {})
        self.url = str(url or config.get("url", "http://127.0.0.1:8765")).rstrip("/")
        self.timeout = float(timeout or config.get("timeout_seconds", 8))
        self._token = token or os.environ.get("NEXO_BRIDGE_TOKEN")
        self._token_path = str(config.get("token_path", "%LOCALAPPDATA%\\Nexo\\bridge-token"))

    @property
    def token(self) -> str:
        if self._token:
            return self._token
        token_path = windows_to_wsl(expand_windows_env(self._token_path))
        try:
            value = Path(token_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise BridgeUnavailable("Token do Windows Bridge não foi encontrado") from exc
        if len(value) < 32:
            raise BridgeUnavailable("Token do Windows Bridge é inválido")
        self._token = value
        return value

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        body = json.dumps(
            {"method": str(method), "params": dict(params or {})},
            ensure_ascii=False,
        ).encode("utf-8")
        payload = None
        last_error = None
        for base_url in self._candidate_urls():
            request = urllib.request.Request(
                base_url + "/rpc",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self.token,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.url = base_url
                break
            except urllib.error.HTTPError as exc:
                try:
                    payload = json.loads(exc.read().decode("utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    last_error = exc
                    payload = None
                    continue
                self.url = base_url
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
        if payload is None:
            try:
                payload = self._call_via_windows_proxy(body)
            except BridgeUnavailable:
                raise
            except Exception as exc:
                raise BridgeUnavailable("Windows Bridge não está acessível") from (last_error or exc)
        if not payload.get("ok"):
            error = payload.get("error") or {}
            raise BridgeError(str(error.get("message") or "Falha no Windows Bridge"))
        return payload.get("result")

    def _candidate_urls(self) -> list[str]:
        return [self.url]

    def _call_via_windows_proxy(self, body: bytes) -> dict[str, Any]:
        profile = expand_windows_env("%USERPROFILE%")
        proxy_windows = profile + "\\AppData\\Local\\Nexo\\bridge\\proxy.py"
        proxy_wsl = windows_to_wsl(proxy_windows)
        launcher = Path("/mnt/c/Windows/py.exe")
        if not proxy_wsl.exists() or not launcher.exists():
            raise BridgeUnavailable("Proxy local do Windows Bridge não foi instalado")
        process = subprocess.run(
            [str(launcher), "-3", proxy_windows, "--url", self.url],
            input=body,
            capture_output=True,
            timeout=self.timeout + 3,
            cwd="/mnt/c",
        )
        if process.returncode != 0:
            raise BridgeUnavailable("Proxy local do Windows Bridge falhou")
        return json.loads(process.stdout.decode("utf-8"))

    def health(self) -> dict[str, Any]:
        result = self.call("health")
        return dict(result or {})
