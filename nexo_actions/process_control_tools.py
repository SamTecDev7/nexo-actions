import json
import re
import subprocess
import unicodedata
from difflib import SequenceMatcher


POWERSHELL = (
    "/mnt/c/Windows/System32/"
    "WindowsPowerShell/v1.0/powershell.exe"
)


CLOSE_APP_TOOL = {
    "type": "function",
    "function": {
        "name": "close_app",
        "description": (
            "Fecha um aplicativo do usuário no Windows. "
            "Não pode encerrar processos críticos do sistema. "
            "Use force=true somente quando o usuário pedir explicitamente "
            "para forçar, matar ou encerrar o processo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Nome natural do aplicativo"
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "Força o encerramento. Deve permanecer false "
                        "a menos que o usuário peça explicitamente."
                    )
                }
            },
            "required": ["app_name", "force"],
            "additionalProperties": False
        }
    }
}


ALIASES = {
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",

    "chrome": "chrome",
    "google chrome": "chrome",

    "discord": "discord",

    "chatgpt": "chatgpt",
    "chat gpt": "chatgpt",

    "terminal": "windowsterminal",
    "windows terminal": "windowsterminal",

    "bloco de notas": "notepad",
    "notepad": "notepad",

    "valorant": "valorant",
    "riot": "riotclientservices",
    "cliente riot": "riotclientservices"
}


# Nexo nunca deve encerrar estes processos.
PROTECTED = {
    "system",
    "registry",
    "smss",
    "csrss",
    "wininit",
    "services",
    "lsass",
    "winlogon",
    "svchost",
    "dwm",
    "explorer",
    "sihost",
    "fontdrvhost",
    "audiodg",

    # segurança
    "msmpeng",
    "securityhealthservice",

    # virtualização / ambiente do Nexo
    "vmmem",
    "vmcompute",
    "wslservice",

    # shells / infraestrutura
    "conhost",
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        c for c in text
        if not unicodedata.combining(c)
    )
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _ps_json(script: str):
    proc = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15
    )

    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip()
        )

    raw = proc.stdout.strip().lstrip("\ufeff")

    if not raw:
        return []

    data = json.loads(raw)

    if isinstance(data, dict):
        return [data]

    return data


def _running_processes():
    script = r'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Get-Process |
ForEach-Object {
    [ordered]@{
        name = $_.ProcessName
        pid = $_.Id
        title = $_.MainWindowTitle
        has_window = ($_.MainWindowHandle -ne 0)
    }
} |
ConvertTo-Json -Compress
'''

    return _ps_json(script)


def _resolve_process(app_name: str):
    requested = _normalize(app_name)
    target = ALIASES.get(requested, requested)

    processes = _running_processes()

    names = {}

    for proc in processes:
        norm = _normalize(proc["name"])
        names.setdefault(norm, []).append(proc)

    # Match exato primeiro.
    if target in names:
        return target, names[target], 100.0

    candidates = []

    for norm, items in names.items():
        score = SequenceMatcher(
            None,
            target,
            norm
        ).ratio() * 100

        if target in norm or norm in target:
            score = max(score, 88)

        candidates.append(
            (score, norm, items)
        )

    candidates.sort(
        reverse=True,
        key=lambda x: x[0]
    )

    if not candidates or candidates[0][0] < 65:
        raise LookupError(
            f"Não encontrei processo para '{app_name}'"
        )

    score, norm, items = candidates[0]

    return norm, items, round(score, 1)


def close_app(app_name: str, force: bool = False):
    app_name = app_name.strip()

    if not app_name:
        raise ValueError(
            "app_name não pode ser vazio"
        )

    process_name, processes, score = (
        _resolve_process(app_name)
    )

    if process_name in PROTECTED:
        return {
            "ok": False,
            "tool": "close_app",
            "blocked": True,
            "reason": "protected_process",
            "requested": app_name,
            "matched": process_name
        }

    pids = [
        int(p["pid"])
        for p in processes
    ]

    if force:
        pid_list = ",".join(
            str(pid)
            for pid in pids
        )

        script = f'''
$ids = @({pid_list})

foreach ($id in $ids) {{
    try {{
        Stop-Process -Id $id -Force -ErrorAction Stop
    }}
    catch {{
    }}
}}
'''

        subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script
            ],
            capture_output=True,
            text=True,
            timeout=15
        )

    else:
        # Fechamento normal:
        # pede para cada janela encerrar corretamente.
        pid_list = ",".join(
            str(pid)
            for pid in pids
        )

        script = f'''
$ids = @({pid_list})

foreach ($id in $ids) {{
    try {{
        $p = Get-Process -Id $id -ErrorAction Stop

        if ($p.MainWindowHandle -ne 0) {{
            $null = $p.CloseMainWindow()
        }}
    }}
    catch {{
    }}
}}

Start-Sleep -Milliseconds 700
'''

        subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script
            ],
            capture_output=True,
            text=True,
            timeout=15
        )

    # Confere o que ainda está executando.
    running = _running_processes()

    remaining = [
        p
        for p in running
        if _normalize(p["name"]) == process_name
    ]

    return {
        "ok": True,
        "tool": "close_app",
        "requested": app_name,
        "matched": process_name,
        "match_score": score,
        "force": bool(force),
        "instances_before": len(processes),
        "instances_remaining": len(remaining),
        "closed": len(remaining) < len(processes)
    }
