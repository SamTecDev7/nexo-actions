"""Account routing and provider-independent email operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..config import load_config, windows_to_wsl, wsl_to_windows
from ..file_tools import _resolve_path, _safe_filename
from .base import EmailProvider
from .gmail_provider import GmailProvider
from .imap_provider import IMAPProvider


PROVIDERS: dict[str, Callable[[str, dict[str, Any]], EmailProvider]] = {
    "imap": IMAPProvider,
    "gmail": GmailProvider,
}


class EmailService:
    def __init__(self, config: dict[str, Any] | None = None, providers=None):
        self.config = config or load_config().get("email", {})
        self.provider_types = providers or PROVIDERS

    def accounts(self) -> list[dict[str, Any]]:
        output = []
        for name, item in self._account_configs().items():
            output.append({
                "name": name,
                "provider": item.get("type"),
                "address": item.get("address") or item.get("username"),
                "configured": item.get("type") in self.provider_types,
            })
        return output

    def provider(self, account: str) -> EmailProvider:
        configs = self._account_configs()
        matched = next((name for name in configs if name.casefold() == str(account).casefold()), None)
        if not matched:
            raise LookupError(f"Conta de e-mail não configurada: {account}")
        config = configs[matched]
        provider_type = str(config.get("type") or "").casefold()
        factory = self.provider_types.get(provider_type)
        if not factory:
            raise ValueError(f"Provider de e-mail desconhecido: {provider_type}")
        return factory(matched, config)

    def search(self, account: str, query: str, limit: int = 10):
        return self.provider(account).search(query, limit)

    def find_latest(self, account: str, query: str):
        results = self.search(account, query, 1)
        return results[0] if results else None

    def read(self, account: str, message_id: str):
        return self.provider(account).read(message_id)

    def attachments(self, account: str, message_id: str):
        return self.provider(account).attachments(message_id)

    def send(self, account: str, to: str, subject: str, body: str):
        return self.provider(account).send(to, subject, body)

    def reply(self, account: str, message_id: str, body: str):
        return self.provider(account).reply(message_id, body)

    def forward(self, account: str, message_id: str, to: str, note: str = ""):
        return self.provider(account).forward(message_id, to, note)

    def save_attachment(
        self,
        account: str,
        message_id: str,
        attachment_id: str,
        directory: str = "downloads",
        filename: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        provider = self.provider(account)
        original_name, payload = provider.attachment_bytes(message_id, attachment_id)
        target_dir = _resolve_path(directory, must_exist=True)
        target = target_dir / _safe_filename(filename or original_name)
        if target.exists() and not overwrite:
            return {"ok": False, "conflict": True, "path": wsl_to_windows(target)}
        mode = "wb" if overwrite else "xb"
        with target.open(mode) as handle:
            handle.write(payload)
        return {
            "ok": True,
            "path": wsl_to_windows(target),
            "bytes": len(payload),
            "filename": target.name,
        }

    def _account_configs(self) -> dict[str, dict[str, Any]]:
        providers = self.config.get("providers", {})
        if not isinstance(providers, dict):
            raise ValueError("email.providers deve ser um objeto")
        return providers
