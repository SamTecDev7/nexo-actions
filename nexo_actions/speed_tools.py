import json
import subprocess


SPEEDTEST = "/usr/bin/speedtest"


SPEED_TEST_TOOL = {
    "type": "function",
    "function": {
        "name": "speed_test",
        "description": (
            "Executa um teste real da velocidade da internet, medindo "
            "download, upload, ping, jitter e perda de pacotes. "
            "Use quando o usuário perguntar pela velocidade real da internet."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False
        }
    }
}


def speed_test():
    proc = subprocess.run(
        [
            SPEEDTEST,
            "--accept-license",
            "--accept-gdpr",
            "--format=json"
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60
    )

    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip()
            or proc.stdout.strip()
            or "Speedtest falhou"
        )

    raw = proc.stdout.strip().lstrip("\ufeff")
    data = json.loads(raw)

    download = data.get("download", {})
    upload = data.get("upload", {})
    ping = data.get("ping", {})
    server = data.get("server", {})
    interface = data.get("interface", {})

    # Ookla retorna bandwidth em bytes/s.
    download_mbps = round(
        float(download.get("bandwidth", 0)) * 8 / 1_000_000,
        1
    )

    upload_mbps = round(
        float(upload.get("bandwidth", 0)) * 8 / 1_000_000,
        1
    )

    return {
        "ok": True,
        "tool": "speed_test",

        "download_mbps": download_mbps,
        "upload_mbps": upload_mbps,

        "ping_ms": round(
            float(ping.get("latency", 0)),
            1
        ),

        "jitter_ms": round(
            float(ping.get("jitter", 0)),
            1
        ),

        "packet_loss_percent": data.get(
            "packetLoss"
        ),

        "loaded_latency": {
            "download_ms": round(
                float(
                    download
                    .get("latency", {})
                    .get("iqm", 0)
                ),
                1
            ),
            "upload_ms": round(
                float(
                    upload
                    .get("latency", {})
                    .get("iqm", 0)
                ),
                1
            )
        },

        "isp": data.get("isp"),

        "server": {
            "name": server.get("name"),
            "location": server.get("location"),
            "country": server.get("country")
        },

        "interface": {
            "name": interface.get("name"),
            "internal_ip": interface.get("internalIp"),
            "external_ip": interface.get("externalIp"),
            "vpn": interface.get("isVpn")
        }
    }
