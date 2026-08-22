"""Generic TLS IMAP provider."""

from __future__ import annotations

import email
import imaplib
import json
import os
import smtplib
import ssl
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from email.message import EmailMessage
from email.utils import parseaddr

from ..config import expand_windows_env, windows_to_wsl
from .base import EmailProvider, decode_header, message_attachments, message_text


class IMAPProvider(EmailProvider):
    def _secret(self) -> str:
        env_name = self.config.get("password_env")
        if env_name and os.environ.get(str(env_name)):
            return os.environ[str(env_name)]
        secret_path = self.config.get("secret_file")
        if not secret_path:
            raise RuntimeError(f"Conta {self.account} não possui secret_file/password_env")
        expanded = expand_windows_env(str(secret_path))
        path = windows_to_wsl(expanded) if len(expanded) > 2 and expanded[1:3] == ":\\" else Path(expanded).expanduser()
        mode = stat.S_IMODE(path.stat().st_mode)
        if os.name != "nt" and mode & 0o077:
            raise PermissionError("O arquivo de segredo IMAP precisa ter permissão 600")
        data = json.loads(path.read_text(encoding="utf-8"))
        password = str(data.get("password") or "")
        if not password:
            raise RuntimeError("Senha IMAP ausente")
        return password

    @contextmanager
    def _connection(self, *, readonly: bool = True):
        host = str(self.config.get("host") or "").strip()
        username = str(self.config.get("username") or "").strip()
        if not host or not username:
            raise RuntimeError("host e username são obrigatórios para IMAP")
        port = int(self.config.get("port", 993))
        mailbox = str(self.config.get("mailbox", "INBOX"))
        connection = imaplib.IMAP4_SSL(host, port, timeout=15)
        try:
            connection.login(username, self._secret())
            status, _data = connection.select(mailbox, readonly=readonly)
            if status != "OK":
                raise RuntimeError(f"Não foi possível selecionar {mailbox}")
            yield connection
        finally:
            try:
                connection.logout()
            except Exception:
                pass

    @staticmethod
    def _quoted_search(query: str) -> str:
        query = " ".join(str(query).split()).strip()
        if "\r" in query or "\n" in query:
            raise ValueError("Consulta IMAP inválida")
        return '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"'

    @staticmethod
    def _fetch_message(connection, uid: str):
        status, data = connection.uid("fetch", uid, "(RFC822)")
        if status != "OK":
            raise LookupError(f"Mensagem IMAP não encontrada: {uid}")
        for item in data:
            if isinstance(item, tuple) and isinstance(item[1], bytes):
                return email.message_from_bytes(item[1])
        raise LookupError(f"Mensagem IMAP vazia: {uid}")

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 50))
        with self._connection() as connection:
            criteria = ("ALL",) if not str(query).strip() else ("TEXT", self._quoted_search(query))
            status, data = connection.uid("search", None, *criteria)
            if status != "OK":
                raise RuntimeError("Busca IMAP falhou")
            uids = (data[0] or b"").decode("ascii", errors="ignore").split()
            results = []
            for uid in reversed(uids[-limit:]):
                message = self._fetch_message(connection, uid)
                results.append(self._metadata(uid, message))
            return results

    def read(self, message_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            message = self._fetch_message(connection, str(message_id))
        return {
            **self._metadata(str(message_id), message),
            "body": message_text(message),
            "attachments": message_attachments(message),
        }

    def attachments(self, message_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            message = self._fetch_message(connection, str(message_id))
        return message_attachments(message)

    def attachment_bytes(self, message_id: str, attachment_id: str) -> tuple[str, bytes]:
        with self._connection() as connection:
            message = self._fetch_message(connection, str(message_id))
        try:
            index = int(str(attachment_id).removeprefix("part-"))
            part = list(message.walk())[index]
        except (ValueError, IndexError) as exc:
            raise LookupError("Anexo IMAP não encontrado") from exc
        filename = decode_header(part.get_filename()) or f"attachment-{index}"
        return filename, part.get_payload(decode=True) or b""

    def _send_message(self, message: EmailMessage) -> dict[str, Any]:
        host = str(self.config.get("smtp_host") or "").strip()
        username = str(self.config.get("smtp_username") or self.config.get("username") or "").strip()
        if not host or not username:
            raise RuntimeError("smtp_host/smtp_username não configurados para esta conta")
        use_ssl = bool(self.config.get("smtp_ssl", True))
        port = int(self.config.get("smtp_port", 465 if use_ssl else 587))
        context = ssl.create_default_context()
        connection = (
            smtplib.SMTP_SSL(host, port, timeout=20, context=context)
            if use_ssl
            else smtplib.SMTP(host, port, timeout=20)
        )
        with connection as client:
            if not use_ssl and self.config.get("smtp_starttls", True):
                client.starttls(context=context)
            client.login(username, self._secret())
            response = client.send_message(message)
        return {"sent": True, "refused_recipients": list(response)}

    def send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        _name, address = parseaddr(str(to))
        if not address or "@" not in address:
            raise ValueError("Destinatário de e-mail inválido")
        message = EmailMessage()
        message["From"] = self.config.get("address") or self.config.get("username")
        message["To"] = address
        message["Subject"] = str(subject)
        message.set_content(str(body))
        return {"to": address, "subject": str(subject), **self._send_message(message)}

    def reply(self, message_id: str, body: str) -> dict[str, Any]:
        with self._connection() as connection:
            original = self._fetch_message(connection, str(message_id))
        _name, address = parseaddr(original.get("Reply-To") or original.get("From") or "")
        if not address:
            raise ValueError("Remetente original não possui endereço válido")
        subject = decode_header(original.get("Subject"))
        if not subject.casefold().startswith("re:"):
            subject = "Re: " + subject
        message = EmailMessage()
        message["From"] = self.config.get("address") or self.config.get("username")
        message["To"] = address
        message["Subject"] = subject
        original_id = original.get("Message-ID")
        if original_id:
            message["In-Reply-To"] = original_id
            message["References"] = original_id
        message.set_content(str(body))
        return {"message_id": str(message_id), **self._send_message(message)}

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
        return {"message_id": str(message_id), **self.send(to, subject, forwarded)}

    @staticmethod
    def _metadata(uid: str, message) -> dict[str, Any]:
        return {
            "message_id": str(uid),
            "subject": decode_header(message.get("Subject")),
            "from": decode_header(message.get("From")),
            "date": decode_header(message.get("Date")),
            "snippet": message_text(message, limit=400),
            "has_attachments": bool(message_attachments(message)),
        }
