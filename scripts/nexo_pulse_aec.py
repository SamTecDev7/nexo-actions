#!/usr/bin/env python3
"""Run an isolated PulseAudio WebRTC echo canceller for Nexo on WSLg."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path


PULSE_SOCKET = Path("/tmp/nexo-pulse/native")
PULSE_SERVER = f"unix:{PULSE_SOCKET}"


def _notify_systemd(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
        notifier.connect(address)
        notifier.sendall(message.encode("utf-8"))


def _run_pactl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PULSE_SERVER"] = PULSE_SERVER
    return subprocess.run(
        ["pactl", *args],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    )


def _wait_for_tunnels(process: subprocess.Popen[bytes], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"PulseAudio encerrou durante a inicializacao: {process.returncode}")
        result = _run_pactl("list", "short", "sources", check=False)
        sinks = _run_pactl("list", "short", "sinks", check=False)
        if "NexoWSLgSource" in result.stdout and "NexoWSLgSink" in sinks.stdout:
            return
        time.sleep(0.2)
    raise RuntimeError("Os tuneis RDPSink/RDPSource nao ficaram prontos a tempo")


def _prepare_socket() -> None:
    PULSE_SOCKET.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    PULSE_SOCKET.parent.chmod(0o700)
    if not PULSE_SOCKET.exists() and not PULSE_SOCKET.is_symlink():
        return
    mode = PULSE_SOCKET.lstat().st_mode
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f"Recusando remover caminho inesperado: {PULSE_SOCKET}")
    PULSE_SOCKET.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    root = args.runtime_root.expanduser().resolve()
    server = root / "usr/bin/pulseaudio"
    module_dirs = sorted((root / "usr/lib").glob("pulse-*/modules"))
    core_lib = root / "usr/lib/x86_64-linux-gnu/pulseaudio"
    private_lib = root / "usr/lib/x86_64-linux-gnu"
    if not server.is_file() or not module_dirs:
        raise RuntimeError(f"Runtime PulseAudio incompleto em {root}")

    _prepare_socket()
    module_dir = module_dirs[-1]
    server_env = os.environ.copy()
    server_env.pop("PULSE_SERVER", None)
    # PulseAudio also knows sd_notify.  Only this supervisor may declare the
    # service ready, after WebRTC and both virtual devices really exist.
    server_env.pop("NOTIFY_SOCKET", None)
    server_env["PULSE_RUNTIME_PATH"] = str(PULSE_SOCKET.parent)
    server_env["LD_LIBRARY_PATH"] = ":".join(
        (str(module_dir), str(core_lib), str(private_lib))
    )
    command = [
        str(server),
        "--daemonize=no",
        "--exit-idle-time=-1",
        f"--dl-search-path={module_dir}",
        f"--file={args.config.expanduser().resolve()}",
        "--log-target=journal",
    ]
    process = subprocess.Popen(command, env=server_env)

    def stop_child(_signum: int, _frame: object) -> None:
        if process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, stop_child)
    signal.signal(signal.SIGINT, stop_child)

    try:
        _wait_for_tunnels(process)
        module = _run_pactl(
            "load-module",
            "module-echo-cancel",
            "source_master=NexoWSLgSource",
            "sink_master=NexoWSLgSink",
            "source_name=NexoAECSource",
            "sink_name=NexoAECSink",
            "aec_method=webrtc",
            "use_master_format=1",
            "source_properties=device.description=Nexo_AEC_Microphone",
            "sink_properties=device.description=Nexo_AEC_Output",
        ).stdout.strip()
        _run_pactl("set-default-source", "NexoAECSource")
        _run_pactl("set-default-sink", "NexoAECSink")
        print(f"NEXO_AEC ready=true method=webrtc module={module}", flush=True)
        _notify_systemd("READY=1\nSTATUS=WebRTC AEC ativo em RDPSink/RDPSource")
        return process.wait()
    except Exception:
        _notify_systemd("STATUS=Falha ao iniciar WebRTC AEC")
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"NEXO_AEC ready=false error={error}", file=sys.stderr, flush=True)
        raise
