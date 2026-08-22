import json
import re
import subprocess
import time
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote_plus


POWERSHELL = (
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
)
CMD = "/mnt/c/Windows/System32/cmd.exe"
EXPLORER = "/mnt/c/Windows/explorer.exe"


# ---------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------

BASE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "youtube_search",
            "description": (
                "Abre o YouTube pesquisando uma música, artista, vídeo "
                "ou assunto solicitado pelo usuário."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Conteúdo a pesquisar no YouTube"
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": (
                "Abre um aplicativo instalado no Windows, como Visual "
                "Studio Code, Chrome, ChatGPT, Terminal, Explorador de "
                "Arquivos, Valorant ou Riot Client."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": (
                            "Nome natural do aplicativo solicitado pelo usuário"
                        )
                    }
                },
                "required": ["app_name"],
                "additionalProperties": False
            }
        }
    }
]


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        c for c in text
        if not unicodedata.combining(c)
    )
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


ALIASES = {
    # VS Code
    "vscode": "visual studio code",
    "vs code": "visual studio code",
    "visual code": "visual studio code",
    "code": "visual studio code",

    # Navegador
    "chrome": "google chrome",
    "google": "google chrome",
    "google chrome": "google chrome",

    # Explorador
    "explorer": "explorador de arquivos",
    "explorador": "explorador de arquivos",
    "explorador de arquivo": "explorador de arquivos",
    "arquivos": "explorador de arquivos",

    # Terminal
    "terminal": "terminal",
    "windows terminal": "terminal",
    "terminal do windows": "terminal",
    "ubuntu": "terminal ubuntu",
    "terminal ubuntu": "terminal ubuntu",

    # ChatGPT
    "chat gpt": "chatgpt",
    "chatgpt": "chatgpt",

    # Jogos / Riot
    "valorant": "valorant",
    "riot": "cliente riot",
    "riot client": "cliente riot",
    "cliente da riot": "cliente riot",
    "cliente riot": "cliente riot",
}


_APP_CACHE = {
    "timestamp": 0,
    "apps": []
}


def _get_start_apps():
    """
    Busca os aplicativos registrados no Menu Iniciar do Windows.
    Mantém cache por 60 segundos para evitar PowerShell a cada comando.
    """
    now = time.time()

    if (
        _APP_CACHE["apps"]
        and now - _APP_CACHE["timestamp"] < 60
    ):
        return _APP_CACHE["apps"]

    ps_command = (
        "[Console]::OutputEncoding = "
        "[System.Text.UTF8Encoding]::new(); "
        "Get-StartApps | "
        "Select-Object Name,AppID | "
        "ConvertTo-Json -Compress"
    )

    proc = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            ps_command
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"Get-StartApps falhou: {proc.stderr.strip()}"
        )

    raw = proc.stdout.strip()

    if not raw:
        raise RuntimeError("Get-StartApps retornou vazio")

    data = json.loads(raw)

    if isinstance(data, dict):
        data = [data]

    apps = [
        {
            "name": str(item.get("Name", "")).strip(),
            "appid": str(item.get("AppID", "")).strip()
        }
        for item in data
        if item.get("Name") and item.get("AppID")
    ]

    _APP_CACHE["timestamp"] = now
    _APP_CACHE["apps"] = apps

    return apps


def _score_app(query: str, app_name: str) -> float:
    q = _normalize(query)
    n = _normalize(app_name)

    if q == n:
        return 100.0

    # Ex.: "explorador" dentro de "explorador de arquivos"
    if q in n:
        return 90.0 - min(20, abs(len(n) - len(q)))

    if n in q:
        return 88.0 - min(20, abs(len(n) - len(q)))

    q_words = set(q.split())
    n_words = set(n.split())

    overlap = len(q_words & n_words)

    token_score = 0
    if overlap:
        token_score = (
            overlap / max(len(q_words), len(n_words))
        ) * 90

    fuzzy = SequenceMatcher(None, q, n).ratio() * 100

    return max(token_score, fuzzy)


def _resolve_app(app_name: str):
    requested = _normalize(app_name)
    target = ALIASES.get(requested, requested)

    apps = _get_start_apps()

    ranked = sorted(
        (
            (
                _score_app(target, app["name"]),
                app
            )
            for app in apps
        ),
        key=lambda x: x[0],
        reverse=True
    )

    if not ranked:
        raise LookupError(
            f"Nenhum aplicativo encontrado para: {app_name}"
        )

    score, app = ranked[0]

    if score < 55:
        suggestions = [
            item["name"]
            for _, item in ranked[:3]
        ]

        raise LookupError(
            f"Aplicativo '{app_name}' não encontrado. "
            f"Mais próximos: {suggestions}"
        )

    return {
        **app,
        "score": round(score, 2),
        "requested": app_name
    }


# ---------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------

def youtube_search(query: str):
    query = query.strip()

    if not query:
        raise ValueError("query não pode ser vazia")

    url = (
        "https://www.youtube.com/results"
        "?search_query=" + quote_plus(query)
    )

    subprocess.Popen(
        [
            CMD,
            "/c",
            "start",
            "",
            url
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return {
        "ok": True,
        "tool": "youtube_search",
        "query": query,
        "url": url
    }


def open_app(app_name: str):
    app_name = app_name.strip()

    if not app_name:
        raise ValueError("app_name não pode ser vazio")

    app = _resolve_app(app_name)
    appid = app["appid"]

    # Alguns atalhos retornam diretamente um .exe
    if re.match(r"^[A-Za-z]:\\", appid):
        subprocess.Popen(
            [
                CMD,
                "/c",
                "start",
                "",
                appid
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    # Apps identificados por AppID/AUMID
    else:
        subprocess.Popen(
            [
                EXPLORER,
                f"shell:AppsFolder\\{appid}"
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    verification = _wait_for_app_window(
        requested=app_name,
        matched=app["name"],
        timeout_seconds=5.0,
    )

    return {
        "ok": True,
        "tool": "open_app",
        "dispatched": True,
        "requested": app_name,
        "matched": app["name"],
        "appid": appid,
        "score": app["score"],
        **verification,
    }


def _wait_for_app_window(
    *,
    requested: str,
    matched: str,
    timeout_seconds: float,
) -> dict:
    """Poll the bridge until the launched app has visible Windows evidence."""
    queries = {
        _normalize(requested),
        _normalize(matched),
    }
    queries.discard("")
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    last_bridge_error = False

    while True:
        try:
            snapshot = list_windows()
            windows = snapshot.get("windows") or []
            last_bridge_error = False
        except Exception:
            windows = []
            last_bridge_error = True

        for window in windows:
            if not isinstance(window, dict):
                continue
            evidence = " ".join(
                str(window.get(field) or "")
                for field in ("title", "process", "process_path")
            )
            normalized = _normalize(evidence)
            if any(query in normalized for query in queries):
                return {
                    "verified": True,
                    "verification": "visible_window",
                    "window": {
                        "title": str(window.get("title") or ""),
                        "process": str(window.get("process") or ""),
                        "active": bool(window.get("active")),
                    },
                }

        if time.monotonic() >= deadline:
            return {
                "verified": False,
                "verification": (
                    "bridge_unavailable"
                    if last_bridge_error
                    else "window_not_observed"
                ),
            }
        time.sleep(0.25)


# Imports dos handlers existentes. O registro abaixo é a única fonte de
# metadados, schemas e dispatch das tools.
from .system_tools import SYSTEM_STATUS_TOOL, system_status
from .audio_tools import (
    AUDIO_TOOLS,
    get_volume,
    set_volume,
    change_volume,
    set_mute
)
from .audio_input_tools import (
    AUDIO_INPUT_TOOLS,
    audio_input_status,
    repair_audio_input,
)
from .process_tools import (
    PROCESS_TOOLS,
    list_processes,
    process_status
)
from .process_control_tools import (
    CLOSE_APP_TOOL,
    close_app
)
from .network_tools import (
    NETWORK_TOOLS,
    network_status,
    ping_test
)
from .speed_tools import (
    SPEED_TEST_TOOL,
    speed_test
)
from .storage_tools import (
    STORAGE_TOOLS,
    find_large_files,
    find_large_folders,
)
from .browser_tools import (
    BROWSER_TOOLS,
    open_url,
    open_website,
    open_nexo_admin,
    web_search,
)
from .web_research import WEB_RESEARCH_TOOL, web_research
from .desktop_tools import (
    DESKTOP_TOOLS,
    active_window,
    bridge_health,
    focus_window,
    list_windows,
    show_desktop,
    switch_window,
    type_text,
    window_action,
)
from .computer_tools import (
    COMPUTER_TOOLS,
    computer_action,
    observe_desktop,
)
from .memory_tools import (
    MEMORY_TOOLS,
    forget_preference,
    recall_preferences,
    remember_preference,
)
from .file_tools import (
    FILE_TOOLS,
    copy_file,
    create_folder,
    create_text_file,
    delete_file,
    delete_folder,
    find_files,
    move_file,
    open_file,
    open_folder,
    read_text_file,
    rename_file,
    replace_text_in_file,
    reveal_file,
    rewrite_text_file,
)
from .ai_tools import AI_RUNTIME_TOOL, ai_runtime_status
from .email_tools import (
    EMAIL_TOOLS,
    email_accounts,
    email_find_latest,
    email_forward,
    email_list_attachments,
    email_read,
    email_reply,
    email_save_attachment,
    email_search,
    email_send,
)
from .power_tools import POWER_TOOLS, lock_workstation, power_action
from .tool_registry import ToolRegistry, ToolSpec


def _spec(schema, handler, **metadata):
    return ToolSpec.from_ollama_schema(schema, handler, **metadata)


TOOL_REGISTRY = ToolRegistry([
    _spec(BASE_TOOLS[0], youtube_search, timeout=10, category="browser", risk_level="read_only"),
    _spec(BASE_TOOLS[1], open_app, timeout=15, category="apps", risk_level="read_only"),
    _spec(SYSTEM_STATUS_TOOL, system_status, timeout=25, category="system", risk_level="read_only"),
    _spec(AUDIO_TOOLS[0], get_volume, timeout=15, category="audio", risk_level="read_only"),
    _spec(AUDIO_TOOLS[1], set_volume, timeout=15, category="audio", risk_level="low"),
    _spec(AUDIO_TOOLS[2], change_volume, timeout=15, category="audio", risk_level="low"),
    _spec(AUDIO_TOOLS[3], set_mute, timeout=15, category="audio", risk_level="low"),
    _spec(AUDIO_INPUT_TOOLS[0], audio_input_status, timeout=8, category="audio", risk_level="read_only"),
    _spec(
        AUDIO_INPUT_TOOLS[1], repair_audio_input, timeout=75, category="audio",
        risk_level="low", long_running=True,
    ),
    _spec(PROCESS_TOOLS[0], list_processes, timeout=20, category="processes", risk_level="read_only"),
    _spec(PROCESS_TOOLS[1], process_status, timeout=20, category="processes", risk_level="read_only"),
    _spec(CLOSE_APP_TOOL, close_app, timeout=20, category="processes", risk_level="medium"),
    _spec(NETWORK_TOOLS[0], network_status, timeout=25, category="network", risk_level="read_only"),
    _spec(NETWORK_TOOLS[1], ping_test, timeout=25, category="network", risk_level="read_only"),
    _spec(SPEED_TEST_TOOL, speed_test, timeout=70, category="network", risk_level="read_only", long_running=True),
    _spec(STORAGE_TOOLS[0], find_large_files, timeout=28, category="storage", risk_level="read_only", long_running=True),
    _spec(STORAGE_TOOLS[1], find_large_folders, timeout=28, category="storage", risk_level="read_only", long_running=True),
    _spec(BROWSER_TOOLS[0], open_url, timeout=8, category="browser", risk_level="read_only"),
    _spec(BROWSER_TOOLS[1], open_website, timeout=8, category="browser", risk_level="read_only"),
    _spec(BROWSER_TOOLS[2], web_search, timeout=8, category="browser", risk_level="read_only"),
    _spec(BROWSER_TOOLS[3], open_nexo_admin, timeout=8, category="browser", risk_level="read_only"),
    _spec(
        WEB_RESEARCH_TOOL, web_research, timeout=55, category="browser",
        risk_level="read_only", long_running=True,
    ),
    _spec(DESKTOP_TOOLS[0], bridge_health, timeout=5, category="desktop", risk_level="read_only"),
    _spec(DESKTOP_TOOLS[1], list_windows, timeout=8, category="desktop", risk_level="read_only"),
    _spec(DESKTOP_TOOLS[2], active_window, timeout=5, category="desktop", risk_level="read_only"),
    _spec(DESKTOP_TOOLS[3], focus_window, timeout=8, category="desktop", risk_level="low"),
    _spec(DESKTOP_TOOLS[4], window_action, timeout=8, category="desktop", risk_level="medium"),
    _spec(DESKTOP_TOOLS[5], show_desktop, timeout=5, category="desktop", risk_level="low"),
    _spec(
        DESKTOP_TOOLS[6], type_text, timeout=10, category="desktop", risk_level="low",
        log_policy="metadata_only", redact_fields=("text",),
    ),
    _spec(DESKTOP_TOOLS[7], switch_window, timeout=8, category="desktop", risk_level="low"),
    _spec(
        COMPUTER_TOOLS[0], observe_desktop, timeout=15, category="desktop",
        risk_level="read_only", log_policy="none",
    ),
    _spec(
        COMPUTER_TOOLS[1], computer_action, timeout=12, category="desktop",
        risk_level="medium", log_policy="metadata_only", redact_fields=("text",),
    ),
    _spec(
        MEMORY_TOOLS[0], remember_preference, timeout=5, category="memory",
        risk_level="low", log_policy="none",
    ),
    _spec(
        MEMORY_TOOLS[1], recall_preferences, timeout=5, category="memory",
        risk_level="read_only", log_policy="none",
    ),
    _spec(
        MEMORY_TOOLS[2], forget_preference, timeout=5, category="memory",
        risk_level="medium", requires_confirmation=True, log_policy="none",
    ),
    _spec(FILE_TOOLS[0], open_folder, timeout=8, category="files", risk_level="read_only"),
    _spec(FILE_TOOLS[1], find_files, timeout=20, category="files", risk_level="read_only", long_running=True),
    _spec(FILE_TOOLS[2], create_folder, timeout=8, category="files", risk_level="low"),
    _spec(
        FILE_TOOLS[3], create_text_file, timeout=10, category="files", risk_level="low",
        log_policy="metadata_only", redact_fields=("content",),
    ),
    _spec(FILE_TOOLS[4], open_file, timeout=8, category="files", risk_level="read_only"),
    _spec(FILE_TOOLS[5], reveal_file, timeout=8, category="files", risk_level="read_only"),
    _spec(FILE_TOOLS[6], copy_file, timeout=15, category="files", risk_level="medium"),
    _spec(
        FILE_TOOLS[7], move_file, timeout=15, category="files", risk_level="medium",
        requires_confirmation=True,
    ),
    _spec(
        FILE_TOOLS[8], rename_file, timeout=10, category="files", risk_level="medium",
        requires_confirmation=True,
    ),
    _spec(FILE_TOOLS[9], delete_file, timeout=30, category="files", risk_level="high", requires_confirmation=True),
    _spec(FILE_TOOLS[10], delete_folder, timeout=60, category="files", risk_level="high", requires_confirmation=True),
    _spec(
        FILE_TOOLS[11], read_text_file, timeout=8, category="files", risk_level="read_only",
        log_policy="metadata_only",
    ),
    _spec(
        FILE_TOOLS[12], rewrite_text_file, timeout=10, category="files", risk_level="low",
        log_policy="metadata_only", redact_fields=("content",),
    ),
    _spec(
        FILE_TOOLS[13], replace_text_in_file, timeout=10, category="files", risk_level="low",
        log_policy="metadata_only", redact_fields=("search", "replacement"),
    ),
    _spec(AI_RUNTIME_TOOL, ai_runtime_status, timeout=8, category="ai", risk_level="read_only"),
    _spec(EMAIL_TOOLS[0], email_accounts, timeout=5, category="email", risk_level="read_only", log_policy="metadata_only"),
    _spec(EMAIL_TOOLS[1], email_search, timeout=35, category="email", risk_level="read_only", long_running=True, log_policy="metadata_only", redact_fields=("query",)),
    _spec(EMAIL_TOOLS[2], email_find_latest, timeout=35, category="email", risk_level="read_only", long_running=True, log_policy="metadata_only", redact_fields=("query",)),
    _spec(EMAIL_TOOLS[3], email_read, timeout=25, category="email", risk_level="read_only", log_policy="metadata_only"),
    _spec(EMAIL_TOOLS[4], email_list_attachments, timeout=25, category="email", risk_level="read_only", log_policy="metadata_only"),
    _spec(EMAIL_TOOLS[5], email_save_attachment, timeout=35, category="email", risk_level="low", long_running=True, log_policy="metadata_only"),
    _spec(EMAIL_TOOLS[6], email_send, timeout=30, category="email", risk_level="high", requires_confirmation=True, log_policy="metadata_only", redact_fields=("body",)),
    _spec(EMAIL_TOOLS[7], email_reply, timeout=30, category="email", risk_level="high", requires_confirmation=True, log_policy="metadata_only", redact_fields=("body",)),
    _spec(EMAIL_TOOLS[8], email_forward, timeout=30, category="email", risk_level="high", requires_confirmation=True, log_policy="metadata_only", redact_fields=("note",)),
    _spec(POWER_TOOLS[0], lock_workstation, timeout=5, category="power", risk_level="low"),
    _spec(POWER_TOOLS[1], power_action, timeout=8, category="power", risk_level="high", requires_confirmation=True),
])

# API legada preservada para o plugin instalado e scripts existentes.
TOOLS = TOOL_REGISTRY.schemas()
FUNCTIONS = TOOL_REGISTRY.handlers()


def execute_tool(name: str, arguments: dict, *, job_context=None):
    return TOOL_REGISTRY.execute(name, arguments, job_context=job_context)
