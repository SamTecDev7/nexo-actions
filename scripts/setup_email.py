#!/usr/bin/env python3
"""Interactive, provider-neutral email account setup for Nexo."""

from __future__ import annotations

import argparse
import getpass
import json
import re
from pathlib import Path

from nexo_actions.config import DEFAULT_CONFIG_PATH, ensure_config


SECRETS_ROOT = Path.home() / ".config" / "nexo" / "secrets" / "email"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-") or "account"


def _load() -> tuple[Path, dict]:
    path = ensure_config()
    return path, json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, config: dict) -> None:
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _set_provider(account: str, provider: dict) -> None:
    path, config = _load()
    config.setdefault("email", {}).setdefault("providers", {})[account] = provider
    _save(path, config)


def setup_imap(args) -> None:
    provider = {
        "type": "imap",
        "host": args.host,
        "port": args.port,
        "username": args.username,
        "address": args.address or args.username,
        "mailbox": args.mailbox,
        "smtp_host": args.smtp_host,
        "smtp_port": args.smtp_port,
        "smtp_username": args.smtp_username or args.username,
        "smtp_ssl": args.smtp_ssl,
        "smtp_starttls": not args.smtp_ssl,
    }
    if args.password_env:
        provider["password_env"] = args.password_env
    else:
        password = getpass.getpass(f"Senha/app password IMAP de {args.account}: ")
        if not password:
            raise SystemExit("Senha vazia; nenhuma alteração feita")
        SECRETS_ROOT.mkdir(parents=True, exist_ok=True)
        secret_path = SECRETS_ROOT / f"{_slug(args.account)}-imap.json"
        secret_path.write_text(
            json.dumps({"password": password}, ensure_ascii=False),
            encoding="utf-8",
        )
        secret_path.chmod(0o600)
        provider["secret_file"] = str(secret_path)
    _set_provider(args.account, provider)
    print(f"Conta IMAP '{args.account}' configurada sem expor a credencial.")


def setup_gmail(args) -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise SystemExit(
            "Dependências ausentes. Execute: pip install google-auth-oauthlib google-api-python-client"
        ) from exc
    client_file = Path(args.client_secrets).expanduser().resolve()
    if not client_file.exists():
        raise SystemExit(f"Arquivo OAuth não encontrado: {client_file}")
    SECRETS_ROOT.mkdir(parents=True, exist_ok=True)
    token_path = SECRETS_ROOT / f"{_slug(args.account)}-gmail-token.json"
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_file),
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    )
    credentials = flow.run_local_server(port=0, open_browser=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    token_path.chmod(0o600)
    _set_provider(args.account, {
        "type": "gmail",
        "address": args.address,
        "token_file": str(token_path),
    })
    print(f"Conta Gmail '{args.account}' autorizada para leitura e envio confirmado.")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    imap = subparsers.add_parser("imap")
    imap.add_argument("--account", required=True)
    imap.add_argument("--host", required=True)
    imap.add_argument("--port", type=int, default=993)
    imap.add_argument("--username", required=True)
    imap.add_argument("--address")
    imap.add_argument("--mailbox", default="INBOX")
    imap.add_argument("--password-env")
    imap.add_argument("--smtp-host", required=True)
    imap.add_argument("--smtp-port", type=int, default=465)
    imap.add_argument("--smtp-username")
    smtp_mode = imap.add_mutually_exclusive_group()
    smtp_mode.add_argument("--smtp-ssl", dest="smtp_ssl", action="store_true", default=True)
    smtp_mode.add_argument("--smtp-starttls", dest="smtp_ssl", action="store_false")

    gmail = subparsers.add_parser("gmail")
    gmail.add_argument("--account", required=True)
    gmail.add_argument("--address", required=True)
    gmail.add_argument("--client-secrets", required=True)

    subparsers.add_parser("list")
    args = parser.parse_args()
    if args.command == "imap":
        setup_imap(args)
    elif args.command == "gmail":
        setup_gmail(args)
    else:
        _path, config = _load()
        accounts = config.get("email", {}).get("providers", {})
        print(json.dumps({
            name: {"type": item.get("type"), "address": item.get("address")}
            for name, item in accounts.items()
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
