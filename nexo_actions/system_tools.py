import json
import shutil
import socket
import subprocess
import time
from pathlib import Path


POWERSHELL = (
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
)


SYSTEM_STATUS_TOOL = {
    "type": "function",
    "function": {
        "name": "system_status",
        "description": (
            "Consulta o estado atual do computador Windows do usuário. "
            "Use para perguntas sobre CPU, processador, GPU, placa de vídeo, "
            "VRAM, memória RAM, armazenamento, espaço em disco, internet, "
            "rede, temperatura ou estado geral do PC."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": [
                        "all",
                        "cpu",
                        "gpu",
                        "memory",
                        "disk",
                        "internet"
                    ],
                    "description": (
                        "Parte do computador que deve ser consultada. "
                        "Use all para estado geral."
                    )
                }
            },
            "required": ["scope"],
            "additionalProperties": False
        }
    }
}


def _windows_status():
    ps = r'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$processor = Get-CimInstance Win32_Processor |
    Select-Object -First 1

$os = Get-CimInstance Win32_OperatingSystem

$disks = Get-CimInstance Win32_LogicalDisk |
    Where-Object { $_.DriveType -eq 3 }

$totalRam = [double]$os.TotalVisibleMemorySize
$freeRam = [double]$os.FreePhysicalMemory
$usedRam = $totalRam - $freeRam

$result = [ordered]@{
    cpu = [ordered]@{
        name = $processor.Name.Trim()
        usage_percent = [math]::Round(
            [double]$processor.LoadPercentage, 1
        )
    }

    memory = [ordered]@{
        total_gb = [math]::Round($totalRam / 1MB, 2)
        used_gb = [math]::Round($usedRam / 1MB, 2)
        free_gb = [math]::Round($freeRam / 1MB, 2)
        usage_percent = [math]::Round(
            ($usedRam / $totalRam) * 100, 1
        )
    }

    disks = @(
        $disks | ForEach-Object {
            [ordered]@{
                drive = $_.DeviceID
                label = $_.VolumeName
                total_gb = [math]::Round(
                    [double]$_.Size / 1GB, 2
                )
                free_gb = [math]::Round(
                    [double]$_.FreeSpace / 1GB, 2
                )
                used_percent = if ($_.Size -gt 0) {
                    [math]::Round(
                        (
                            (
                                [double]$_.Size -
                                [double]$_.FreeSpace
                            ) /
                            [double]$_.Size
                        ) * 100,
                        1
                    )
                } else {
                    0
                }
            }
        }
    )
}

$result | ConvertTo-Json -Compress -Depth 6
'''

    proc = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            ps
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"Falha consultando Windows: {proc.stderr.strip()}"
        )

    raw = proc.stdout.strip().lstrip("\ufeff")

    if not raw:
        raise RuntimeError("Windows não retornou dados")

    return json.loads(raw)


def _gpu_status():
    nvidia_smi = shutil.which("nvidia-smi")

    if not nvidia_smi:
        wsl_path = Path(
            "/usr/lib/wsl/lib/nvidia-smi"
        )
        if wsl_path.exists():
            nvidia_smi = str(wsl_path)

    if not nvidia_smi:
        return {
            "available": False,
            "reason": "nvidia-smi não encontrado"
        }

    proc = subprocess.run(
        [
            nvidia_smi,
            "--query-gpu="
            "name,"
            "utilization.gpu,"
            "memory.used,"
            "memory.total,"
            "temperature.gpu",
            "--format=csv,noheader,nounits"
        ],
        capture_output=True,
        text=True,
        timeout=8
    )

    if proc.returncode != 0:
        return {
            "available": False,
            "reason": proc.stderr.strip()
        }

    line = proc.stdout.strip().splitlines()[0]

    parts = [
        p.strip()
        for p in line.split(",")
    ]

    if len(parts) < 5:
        return {
            "available": False,
            "reason": "Resposta inesperada do nvidia-smi"
        }

    return {
        "available": True,
        "name": parts[0],
        "usage_percent": float(parts[1]),
        "memory_used_mb": float(parts[2]),
        "memory_total_mb": float(parts[3]),
        "temperature_c": float(parts[4])
    }


def _internet_status():
    dns_ok = False
    online = False
    connect_ms = None

    try:
        socket.gethostbyname("www.google.com")
        dns_ok = True
    except Exception:
        pass

    sock = None

    try:
        start = time.perf_counter()

        sock = socket.create_connection(
            ("1.1.1.1", 443),
            timeout=3
        )

        connect_ms = round(
            (time.perf_counter() - start) * 1000,
            1
        )

        online = True

    except Exception:
        online = False

    finally:
        if sock:
            sock.close()

    return {
        "online": online,
        "dns_ok": dns_ok,
        "connect_test_ms": connect_ms
    }


def system_status(scope: str):
    allowed = {
        "all",
        "cpu",
        "gpu",
        "memory",
        "disk",
        "internet"
    }

    if scope not in allowed:
        raise ValueError(
            f"scope inválido: {scope}"
        )

    result = {
        "ok": True,
        "tool": "system_status",
        "scope": scope
    }

    if scope in {
        "all",
        "cpu",
        "memory",
        "disk"
    }:
        windows = _windows_status()

        if scope in {"all", "cpu"}:
            result["cpu"] = windows["cpu"]

        if scope in {"all", "memory"}:
            result["memory"] = windows["memory"]

        if scope in {"all", "disk"}:
            result["disks"] = windows["disks"]

    if scope in {"all", "gpu"}:
        result["gpu"] = _gpu_status()

    if scope in {"all", "internet"}:
        result["internet"] = _internet_status()

    return result
