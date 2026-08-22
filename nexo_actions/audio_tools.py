import json
import subprocess
from pathlib import Path


POWERSHELL = (
    "/mnt/c/Windows/System32/"
    "WindowsPowerShell/v1.0/powershell.exe"
)

SCRIPT = Path(__file__).with_name(
    "windows_audio.ps1"
)


AUDIO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_volume",
            "description": (
                "Consulta o volume atual do Windows "
                "e informa se o áudio está mutado."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": (
                "Define o volume do Windows para "
                "um percentual exato."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": (
                            "Volume desejado entre 0 e 100"
                        )
                    }
                },
                "required": ["level"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "change_volume",
            "description": (
                "Aumenta ou diminui o volume atual "
                "do Windows em uma quantidade percentual."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "delta": {
                        "type": "integer",
                        "minimum": -100,
                        "maximum": 100,
                        "description": (
                            "Valor positivo para aumentar e "
                            "negativo para diminuir o volume"
                        )
                    }
                },
                "required": ["delta"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_mute",
            "description": (
                "Muta ou desmuta completamente "
                "o áudio do Windows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "muted": {
                        "type": "boolean",
                        "description": (
                            "true para mutar, false para restaurar"
                        )
                    }
                },
                "required": ["muted"],
                "additionalProperties": False
            }
        }
    }
]


def _run(action, value=None):
    cmd = [
        POWERSHELL,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        "-Action",
        action
    ]

    if value is not None:
        cmd += [
            "-Value",
            str(value)
        ]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15
    )

    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip()
            or proc.stdout.strip()
        )

    lines = [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip()
    ]

    if not lines:
        raise RuntimeError(
            "Controle de áudio não retornou dados"
        )

    return json.loads(
        lines[-1].lstrip("\ufeff")
    )


def get_volume():
    return _run("get")


def set_volume(level: int):
    level = max(
        0,
        min(100, int(level))
    )

    return _run(
        "set",
        level
    )


def change_volume(delta: int):
    delta = max(
        -100,
        min(100, int(delta))
    )

    return _run(
        "change",
        delta
    )


def set_mute(muted: bool):
    return _run(
        "mute" if muted else "unmute"
    )
