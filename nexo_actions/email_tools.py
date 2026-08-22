"""Typed, provider-independent email tools."""

from __future__ import annotations

from .email import EmailService


def _service() -> EmailService:
    return EmailService()


def email_accounts() -> dict:
    accounts = _service().accounts()
    return {"ok": True, "tool": "email_accounts", "accounts": accounts, "count": len(accounts)}


def email_search(account: str, query: str, limit: int = 10) -> dict:
    messages = _service().search(account, query, limit)
    return {
        "ok": True, "tool": "email_search", "account": account,
        "query": query, "messages": messages, "found": len(messages),
    }


def email_find_latest(account: str, query: str) -> dict:
    message = _service().find_latest(account, query)
    return {
        "ok": True, "tool": "email_find_latest", "account": account,
        "query": query, "message": message, "found": bool(message),
    }


def email_read(account: str, message_id: str) -> dict:
    message = _service().read(account, message_id)
    return {"ok": True, "tool": "email_read", "account": account, "message": message}


def email_list_attachments(account: str, message_id: str) -> dict:
    attachments = _service().attachments(account, message_id)
    return {
        "ok": True, "tool": "email_list_attachments", "account": account,
        "message_id": message_id, "attachments": attachments, "found": len(attachments),
    }


def email_save_attachment(
    account: str,
    message_id: str,
    attachment_id: str,
    directory: str = "downloads",
    filename: str = "",
    overwrite: bool = False,
) -> dict:
    result = _service().save_attachment(
        account, message_id, attachment_id, directory, filename, overwrite
    )
    return {"tool": "email_save_attachment", "account": account, **result}


def email_send(account: str, to: str, subject: str, body: str) -> dict:
    result = _service().send(account, to, subject, body)
    return {"ok": True, "tool": "email_send", "account": account, **result}


def email_reply(account: str, message_id: str, body: str) -> dict:
    result = _service().reply(account, message_id, body)
    return {"ok": True, "tool": "email_reply", "account": account, **result}


def email_forward(account: str, message_id: str, to: str, note: str = "") -> dict:
    result = _service().forward(account, message_id, to, note)
    return {"ok": True, "tool": "email_forward", "account": account, **result}


EMAIL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "email_accounts",
            "description": "Lista as contas de e-mail configuradas no Nexo.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_search",
            "description": "Pesquisa mensagens em uma conta autorizada e retorna metadados e resumo curto.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account": {"type": "string"}, "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["account", "query"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_find_latest",
            "description": "Encontra a mensagem mais recente que corresponde à consulta em uma conta.",
            "parameters": {
                "type": "object",
                "properties": {"account": {"type": "string"}, "query": {"type": "string"}},
                "required": ["account", "query"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_read",
            "description": "Lê uma mensagem específica já encontrada pelo ID.",
            "parameters": {
                "type": "object",
                "properties": {"account": {"type": "string"}, "message_id": {"type": "string"}},
                "required": ["account", "message_id"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_list_attachments",
            "description": "Lista anexos de uma mensagem sem baixá-los.",
            "parameters": {
                "type": "object",
                "properties": {"account": {"type": "string"}, "message_id": {"type": "string"}},
                "required": ["account", "message_id"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_save_attachment",
            "description": "Salva um anexo específico em Downloads ou outra pasta existente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account": {"type": "string"}, "message_id": {"type": "string"},
                    "attachment_id": {"type": "string"}, "directory": {"type": "string"},
                    "filename": {"type": "string"}, "overwrite": {"type": "boolean"},
                },
                "required": ["account", "message_id", "attachment_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_send",
            "description": "Envia um novo e-mail; sempre exige confirmação antes do envio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account": {"type": "string"}, "to": {"type": "string"},
                    "subject": {"type": "string"}, "body": {"type": "string"},
                },
                "required": ["account", "to", "subject", "body"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_reply",
            "description": "Responde a uma mensagem existente; sempre exige confirmação antes do envio.",
            "parameters": {
                "type": "object",
                "properties": {"account": {"type": "string"}, "message_id": {"type": "string"}, "body": {"type": "string"}},
                "required": ["account", "message_id", "body"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_forward",
            "description": "Encaminha uma mensagem; sempre exige confirmação antes do envio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account": {"type": "string"}, "message_id": {"type": "string"},
                    "to": {"type": "string"}, "note": {"type": "string"},
                },
                "required": ["account", "message_id", "to"], "additionalProperties": False,
            },
        },
    },
]
