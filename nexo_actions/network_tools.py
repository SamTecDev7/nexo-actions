import ipaddress
import json
import re
import subprocess


POWERSHELL = (
    "/mnt/c/Windows/System32/"
    "WindowsPowerShell/v1.0/powershell.exe"
)


NETWORK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "network_status",
            "description": (
                "Consulta o estado detalhado da rede do Windows: "
                "adaptadores ativos, IP local, gateway, DNS, velocidade "
                "do link e conectividade com a internet."
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
            "name": "ping_test",
            "description": (
                "Mede latência e perda de pacotes para um endereço IP "
                "ou hostname. Use para perguntas sobre ping, latência, "
                "instabilidade ou qualidade da conexão."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": (
                            "Destino do teste, por exemplo 1.1.1.1, "
                            "google.com ou riotgames.com"
                        )
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Quantidade de pacotes"
                    }
                },
                "required": ["host", "count"],
                "additionalProperties": False
            }
        }
    }
]


def _run_ps(script: str):
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
        timeout=20
    )

    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip()
            or proc.stdout.strip()
        )

    raw = proc.stdout.strip().lstrip("\ufeff")

    if not raw:
        return None

    return json.loads(raw)


def network_status():
    script = r'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$configs = Get-NetIPConfiguration |
    Where-Object {
        $_.NetAdapter.Status -eq "Up"
    }

$adapters = @(
    foreach ($cfg in $configs) {

        $profile = Get-NetConnectionProfile `
            -InterfaceIndex $cfg.InterfaceIndex `
            -ErrorAction SilentlyContinue

        $ipv4 = @(
            $cfg.IPv4Address |
            ForEach-Object {
                $_.IPAddress
            }
        )

        $ipv6 = @(
            $cfg.IPv6Address |
            Where-Object {
                $_.IPAddress -notlike "fe80:*"
            } |
            ForEach-Object {
                $_.IPAddress
            }
        )

        $dns = @(
            $cfg.DNSServer.ServerAddresses
        )

        [ordered]@{
            name = $cfg.InterfaceAlias
            description = $cfg.InterfaceDescription
            interface_index = $cfg.InterfaceIndex
            status = $cfg.NetAdapter.Status
            link_speed = $cfg.NetAdapter.LinkSpeed

            network_name = if ($profile) {
                $profile.Name
            } else {
                $null
            }

            category = if ($profile) {
                [string]$profile.NetworkCategory
            } else {
                $null
            }

            ipv4 = $ipv4
            ipv6 = $ipv6

            gateway = if ($cfg.IPv4DefaultGateway) {
                $cfg.IPv4DefaultGateway.NextHop
            } else {
                $null
            }

            dns = $dns
        }
    }
)

$dnsOk = $false

try {
    $resolved = [System.Net.Dns]::GetHostAddresses(
        "www.google.com"
    )

    $dnsOk = ($resolved.Count -gt 0)
}
catch {
    $dnsOk = $false
}

$internetOk = Test-NetConnection `
    -ComputerName "1.1.1.1" `
    -Port 443 `
    -InformationLevel Quiet `
    -WarningAction SilentlyContinue

$result = [ordered]@{
    online = [bool]$internetOk
    dns_ok = [bool]$dnsOk
    adapters = $adapters
}

$result | ConvertTo-Json -Compress -Depth 8
'''

    data = _run_ps(script)

    return {
        "ok": True,
        "tool": "network_status",
        **(data or {})
    }


def _validate_host(host: str) -> str:
    host = host.strip()

    if not host:
        raise ValueError("host não pode ser vazio")

    # IP válido
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    # hostname simples; evita injeção no PowerShell.
    if not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?",
        host
    ):
        raise ValueError(
            f"hostname inválido: {host}"
        )

    return host


def ping_test(host: str, count: int = 4):
    host = _validate_host(host)
    count = max(1, min(10, int(count)))

    # host foi validado antes de entrar no script.
    script = f'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$requested = {count}

$responses = @(
    Test-Connection `
        -ComputerName "{host}" `
        -Count $requested `
        -ErrorAction SilentlyContinue
)

$success = @(
    $responses |
    Where-Object {{
        $_.StatusCode -eq 0
    }}
)

$times = @(
    $success |
    ForEach-Object {{
        [double]$_.ResponseTime
    }}
)

$received = $success.Count
$lost = $requested - $received

if ($times.Count -gt 0) {{
    $avg = (
        $times |
        Measure-Object -Average
    ).Average

    $min = (
        $times |
        Measure-Object -Minimum
    ).Minimum

    $max = (
        $times |
        Measure-Object -Maximum
    ).Maximum
}}
else {{
    $avg = $null
    $min = $null
    $max = $null
}}

$result = [ordered]@{{
    host = "{host}"
    sent = $requested
    received = $received
    lost = $lost
    loss_percent = [math]::Round(
        ($lost / $requested) * 100,
        1
    )
    average_ms = if ($null -ne $avg) {{
        [math]::Round([double]$avg, 1)
    }} else {{
        $null
    }}
    min_ms = if ($null -ne $min) {{
        [math]::Round([double]$min, 1)
    }} else {{
        $null
    }}
    max_ms = if ($null -ne $max) {{
        [math]::Round([double]$max, 1)
    }} else {{
        $null
    }}
}}

$result | ConvertTo-Json -Compress
'''

    data = _run_ps(script)

    return {
        "ok": True,
        "tool": "ping_test",
        **(data or {})
    }
