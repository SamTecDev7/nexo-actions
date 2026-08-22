"""Provider contract and shared MIME helpers."""

from __future__ import annotations

import email.header
import html
import re
from abc import ABC, abstractmethod
from email.message import Message
from typing import Any


def decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, charset in email.header.decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def message_text(message: Message, limit: int = 8000) -> str:
    plain = []
    html_parts = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        disposition = str(part.get("Content-Disposition", "")).casefold()
        if "attachment" in disposition:
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        (plain if content_type == "text/plain" else html_parts).append(text)
    value = "\n".join(plain)
    if not value and html_parts:
        value = re.sub(r"<[^>]+>", " ", "\n".join(html_parts))
        value = html.unescape(value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def message_attachments(message: Message) -> list[dict[str, Any]]:
    attachments = []
    for index, part in enumerate(message.walk()):
        filename = decode_header(part.get_filename())
        disposition = str(part.get("Content-Disposition", "")).casefold()
        if not filename and "attachment" not in disposition:
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append({
            "attachment_id": f"part-{index}",
            "filename": filename or f"attachment-{index}",
            "content_type": part.get_content_type(),
            "size_bytes": len(payload),
        })
    return attachments


class EmailProvider(ABC):
    def __init__(self, account: str, config: dict[str, Any]):
        self.account = account
        self.config = dict(config)

    @abstractmethod
    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def read(self, message_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def attachments(self, message_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def attachment_bytes(self, message_id: str, attachment_id: str) -> tuple[str, bytes]:
        raise NotImplementedError

    def send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        raise NotImplementedError(f"Provider {type(self).__name__} não suporta envio")

    def reply(self, message_id: str, body: str) -> dict[str, Any]:
        raise NotImplementedError(f"Provider {type(self).__name__} não suporta resposta")

    def forward(self, message_id: str, to: str, note: str = "") -> dict[str, Any]:
        raise NotImplementedError(f"Provider {type(self).__name__} não suporta encaminhamento")
