"""Official Gmail API OAuth provider, loaded only when dependencies exist."""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path
from typing import Any
from email.message import EmailMessage
from email.utils import parseaddr

from .base import EmailProvider


class GmailProvider(EmailProvider):
    def _service(self):
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "Dependências Gmail ausentes; execute scripts/setup_email.py gmail"
            ) from exc
        token_file = Path(str(self.config.get("token_file") or "")).expanduser()
        if not token_file.exists():
            raise RuntimeError(f"Token OAuth não encontrado para {self.account}")
        credentials = Credentials.from_authorized_user_file(
            str(token_file),
            scopes=[
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
            ],
        )
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    @staticmethod
    def _headers(message: dict[str, Any]) -> dict[str, str]:
        return {
            str(item.get("name", "")).casefold(): str(item.get("value", ""))
            for item in (message.get("payload", {}).get("headers") or [])
        }

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        service = self._service()
        response = service.users().messages().list(
            userId="me",
            q=str(query).strip() or None,
            maxResults=max(1, min(int(limit), 50)),
        ).execute()
        results = []
        for item in response.get("messages", []):
            message = service.users().messages().get(
                userId="me",
                id=item["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()
            results.append(self._metadata(message))
        return results

    def read(self, message_id: str) -> dict[str, Any]:
        message = self._service().users().messages().get(
            userId="me", id=str(message_id), format="full"
        ).execute()
        return {
            **self._metadata(message),
            "body": self._body(message.get("payload") or {}),
            "attachments": self._attachments(message.get("payload") or {}),
        }

    def attachments(self, message_id: str) -> list[dict[str, Any]]:
        message = self._service().users().messages().get(
            userId="me", id=str(message_id), format="full"
        ).execute()
        return self._attachments(message.get("payload") or {})

    def attachment_bytes(self, message_id: str, attachment_id: str) -> tuple[str, bytes]:
        service = self._service()
        message = service.users().messages().get(
            userId="me", id=str(message_id), format="full"
        ).execute()
        matches = [
            item for item in self._attachments(message.get("payload") or {})
            if item["attachment_id"] == attachment_id
        ]
        if not matches:
            raise LookupError("Anexo Gmail não encontrado")
        response = service.users().messages().attachments().get(
            userId="me", messageId=str(message_id), id=attachment_id
        ).execute()
        return matches[0]["filename"], self._decode(response.get("data", ""))

    def _send_message(self, message: EmailMessage, thread_id: str | None = None) -> dict[str, Any]:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        body = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        sent = self._service().users().messages().send(userId="me", body=body).execute()
        return {"sent": True, "message_id": sent.get("id"), "thread_id": sent.get("threadId")}

    def send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        _name, address = parseaddr(str(to))
        if not address or "@" not in address:
            raise ValueError("Destinatário de e-mail inválido")
        message = EmailMessage()
        message["To"] = address
        if self.config.get("address"):
            message["From"] = self.config["address"]
        message["Subject"] = str(subject)
        message.set_content(str(body))
        return {"to": address, "subject": str(subject), **self._send_message(message)}

    def reply(self, message_id: str, body: str) -> dict[str, Any]:
        original = self._service().users().messages().get(
            userId="me", id=str(message_id), format="full"
        ).execute()
        headers = self._headers(original)
        _name, address = parseaddr(headers.get("reply-to") or headers.get("from") or "")
        if not address:
            raise ValueError("Remetente original não possui endereço válido")
        subject = headers.get("subject", "")
        if not subject.casefold().startswith("re:"):
            subject = "Re: " + subject
        message = EmailMessage()
        message["To"] = address
        if self.config.get("address"):
            message["From"] = self.config["address"]
        message["Subject"] = subject
        if headers.get("message-id"):
            message["In-Reply-To"] = headers["message-id"]
            message["References"] = headers["message-id"]
        message.set_content(str(body))
        return {
            "original_message_id": str(message_id),
            **self._send_message(message, original.get("threadId")),
        }

    def forward(self, message_id: str, to: str, note: str = "") -> dict[str, Any]:
        original = self.read(message_id)
        subject = str(original.get("subject") or "")
        if not subject.casefold().startswith("fwd:"):
            subject = "Fwd: " + subject
        forwarded = (
            (str(note).strip() + "\n\n" if str(note).strip() else "")
            + "--- Mensagem encaminhada ---\n"
            + f"De: {original.get('from', '')}\nData: {original.get('date', '')}\n"
            + f"Assunto: {original.get('subject', '')}\n\n{original.get('body', '')}"
        )
        return {"original_message_id": str(message_id), **self.send(to, subject, forwarded)}

    @classmethod
    def _metadata(cls, message: dict[str, Any]) -> dict[str, Any]:
        headers = cls._headers(message)
        return {
            "message_id": message.get("id"),
            "thread_id": message.get("threadId"),
            "subject": headers.get("subject", ""),
            "from": headers.get("from", ""),
            "date": headers.get("date", ""),
            "snippet": message.get("snippet", ""),
            "has_attachments": bool(cls._attachments(message.get("payload") or {})),
        }

    @classmethod
    def _walk(cls, payload: dict[str, Any]):
        yield payload
        for part in payload.get("parts") or []:
            yield from cls._walk(part)

    @classmethod
    def _attachments(cls, payload: dict[str, Any]) -> list[dict[str, Any]]:
        output = []
        for part in cls._walk(payload):
            body = part.get("body") or {}
            attachment_id = body.get("attachmentId")
            filename = part.get("filename")
            if not attachment_id or not filename:
                continue
            output.append({
                "attachment_id": attachment_id,
                "filename": filename,
                "content_type": part.get("mimeType", "application/octet-stream"),
                "size_bytes": body.get("size", 0),
            })
        return output

    @classmethod
    def _body(cls, payload: dict[str, Any]) -> str:
        html_value = ""
        for part in cls._walk(payload):
            mime = part.get("mimeType")
            data = (part.get("body") or {}).get("data")
            if not data:
                continue
            value = cls._decode(data).decode("utf-8", errors="replace")
            if mime == "text/plain":
                return cls._short(value)
            if mime == "text/html" and not html_value:
                html_value = value
        if html_value:
            value = html.unescape(re.sub(r"<[^>]+>", " ", html_value))
            return cls._short(value)
        return ""

    @staticmethod
    def _decode(value: str) -> bytes:
        value += "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value.encode("ascii"))

    @staticmethod
    def _short(value: str, limit: int = 8000) -> str:
        value = " ".join(value.split())
        return value if len(value) <= limit else value[: limit - 1] + "…"
