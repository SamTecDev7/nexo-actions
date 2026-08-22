"""Fast, typed browser and website actions."""

from __future__ import annotations

import ipaddress
import re
import subprocess
import unicodedata
from urllib.parse import quote_plus, urlsplit, urlunsplit

from .config import load_config


CMD = "/mnt/c/Windows/System32/cmd.exe"


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value.casefold())
    return " ".join(value.split())


def validate_url(value: str) -> str:
    """Normalize an HTTP(S) URL and reject credentials/invalid hosts."""
    raw = str(value).strip()
    if not raw:
        raise ValueError("URL não pode ser vazia")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw) is None:
        raw = "https://" + raw
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("Somente URLs HTTP e HTTPS são permitidas")
    if parsed.username or parsed.password:
        raise ValueError("URLs com credenciais embutidas não são permitidas")
    host = parsed.hostname
    if not host or any(char.isspace() for char in host):
        raise ValueError("Domínio inválido")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Domínio inválido") from exc
    if ascii_host != "localhost":
        try:
            ipaddress.ip_address(ascii_host)
        except ValueError:
            labels = ascii_host.rstrip(".").split(".")
            if (
                len(labels) < 2
                or any(
                    not label
                    or len(label) > 63
                    or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
                    for label in labels
                )
            ):
                raise ValueError("Domínio inválido")
    netloc = ascii_host
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit(
        (parsed.scheme.casefold(), netloc, parsed.path or "", parsed.query, parsed.fragment)
    )


def _launch_url(url: str, browser: str | None = None) -> None:
    if browser:
        executable = {
            "chrome": "chrome.exe",
            "google chrome": "chrome.exe",
            "edge": "msedge.exe",
            "microsoft edge": "msedge.exe",
            "firefox": "firefox.exe",
        }.get(_normalize(browser))
        if executable:
            subprocess.Popen(
                [CMD, "/d", "/c", "start", "", executable, url],
                cwd="/mnt/c",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    subprocess.Popen(
        [CMD, "/d", "/c", "start", "", url],
        cwd="/mnt/c",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def open_url(url: str, browser: str | None = None) -> dict:
    normalized = validate_url(url)
    _launch_url(normalized, browser)
    return {
        "ok": True,
        "tool": "open_url",
        "url": normalized,
        "browser": browser or "default",
    }


def web_search(query: str, browser: str | None = None) -> dict:
    query = " ".join(str(query).split()).strip()
    if not query:
        raise ValueError("A pesquisa não pode ser vazia")
    url = "https://www.google.com/search?q=" + quote_plus(query)
    _launch_url(url, browser)
    return {
        "ok": True,
        "tool": "web_search",
        "query": query,
        "url": url,
        "browser": browser or "default",
    }


def open_nexo_admin(browser: str | None = None) -> dict:
    """Open the loopback-only protected Nexo control panel."""
    ui = load_config().get("ui", {})
    port = int(ui.get("port", 8787))
    if port < 1 or port > 65535:
        raise ValueError("Porta da interface do Nexo inválida")
    url = f"http://127.0.0.1:{port}/admin"
    _launch_url(url, browser)
    return {"ok": True, "tool": "open_nexo_admin", "url": url, "browser": browser or "default"}


def _looks_like_domain(value: str) -> bool:
    candidate = value.strip()
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", candidate):
        return True
    return bool(re.fullmatch(
        r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}(?:/[^\s]*)?",
        candidate,
    ))


def open_website(name_or_domain: str, browser: str | None = None) -> dict:
    requested = " ".join(str(name_or_domain).split()).strip()
    if not requested:
        raise ValueError("O site não pode ser vazio")
    if _looks_like_domain(requested):
        result = open_url(requested, browser)
        result.update({"tool": "open_website", "requested": requested, "matched_alias": None})
        return result

    aliases = {
        _normalize(key): value
        for key, value in load_config().get("site_aliases", {}).items()
    }
    matched = aliases.get(_normalize(requested))
    if matched:
        result = open_url(str(matched), browser)
        result.update({"tool": "open_website", "requested": requested, "matched_alias": requested})
        return result

    # Unknown names become an explicit search, never an invented domain.
    result = web_search(f"site oficial {requested}", browser)
    result.update({
        "tool": "open_website",
        "requested": requested,
        "matched_alias": None,
        "fallback": "web_search",
    })
    return result


BROWSER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Abre uma URL HTTP ou HTTPS validada no navegador do Windows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "browser": {"type": "string"},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": (
                "Abre um site por nome natural, alias configurado ou domínio. "
                "Se não conhecer o nome, abre uma busca pelo site oficial."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name_or_domain": {"type": "string"},
                    "browser": {"type": "string"},
                },
                "required": ["name_or_domain"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Pesquisa um assunto no Google usando o navegador do Windows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "browser": {"type": "string"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_nexo_admin",
            "description": "Abre o painel administrativo protegido do Nexo no computador local.",
            "parameters": {
                "type": "object",
                "properties": {"browser": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
]
