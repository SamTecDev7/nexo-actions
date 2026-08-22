import json
import subprocess


POWERSHELL = (
    "/mnt/c/Windows/System32/"
    "WindowsPowerShell/v1.0/powershell.exe"
)


PROCESS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_processes",
            "description": (
                "Lista os processos que mais consomem CPU ou memória RAM "
                "no computador Windows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sort_by": {
                        "type": "string",
                        "enum": ["cpu", "memory"],
                        "description": "Recurso usado para ordenar os processos"
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 15,
                        "description": "Quantidade de processos a retornar"
                    }
                },
                "required": ["sort_by", "limit"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "process_status",
            "description": (
                "Consulta quanto um aplicativo ou processo específico "
                "está consumindo de CPU e memória."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Nome do aplicativo ou processo, como Chrome, "
                            "Code, Valorant ou Ollama"
                        )
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    }
]


def _powershell_json(script):
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


def _get_processes():
    ps = r'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$logical = (
    Get-CimInstance Win32_ComputerSystem
).NumberOfLogicalProcessors

$data = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process |
    Where-Object {
        $_.Name -notin @(
            "_Total",
            "Idle"
        ) -and
        $_.IDProcess -gt 0
    } |
    ForEach-Object {
        [ordered]@{
            name = $_.Name
            pid = [int]$_.IDProcess
            cpu_percent = [math]::Round(
                ([double]$_.PercentProcessorTime / $logical),
                1
            )
            memory_mb = [math]::Round(
                ([double]$_.WorkingSetPrivate / 1MB),
                1
            )
        }
    }

$data | ConvertTo-Json -Compress
'''

    return _powershell_json(ps)


def list_processes(sort_by: str, limit: int):
    if sort_by not in {"cpu", "memory"}:
        raise ValueError("sort_by deve ser cpu ou memory")

    limit = max(1, min(15, int(limit)))

    processes = _get_processes()

    key = (
        "cpu_percent"
        if sort_by == "cpu"
        else "memory_mb"
    )

    processes.sort(
        key=lambda p: p.get(key, 0),
        reverse=True
    )

    return {
        "ok": True,
        "tool": "list_processes",
        "sort_by": sort_by,
        "processes": processes[:limit]
    }


def process_status(query: str):
    query = query.strip().casefold()

    if not query:
        raise ValueError("query não pode ser vazia")

    processes = _get_processes()

    matches = [
        p for p in processes
        if query in p["name"].casefold()
    ]

    if not matches:
        return {
            "ok": False,
            "tool": "process_status",
            "query": query,
            "found": False,
            "processes": []
        }

    total_cpu = round(
        sum(p["cpu_percent"] for p in matches),
        1
    )

    total_memory = round(
        sum(p["memory_mb"] for p in matches),
        1
    )

    return {
        "ok": True,
        "tool": "process_status",
        "query": query,
        "found": True,
        "instances": len(matches),
        "cpu_percent": total_cpu,
        "memory_mb": total_memory,
        "processes": sorted(
            matches,
            key=lambda p: p["memory_mb"],
            reverse=True
        )
    }
