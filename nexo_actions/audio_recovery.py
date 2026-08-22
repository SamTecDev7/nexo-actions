"""Health monitoring and recovery for Nexo's WSLg microphone chain."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .config import DEFAULT_CONFIG_PATH, load_config


UPSTREAM_SERVER = "unix:/mnt/wslg/PulseServer"
ISOLATED_SERVER = "unix:/tmp/nexo-pulse/native"
UPSTREAM_SOURCE = "RDPSource"
AEC_SOURCE = "NexoAECSource"
WSL_EXE = "/mnt/c/Windows/System32/wsl.exe"
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
DEFAULT_STATUS_PATH = Path.home() / ".local/state/nexo/audio-health.json"
DEFAULT_LOCK_PATH = Path.home() / ".local/state/nexo/audio-repair.lock"
MIN_STREAM_BYTES = 1024


def _audio_settings(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config = load_config(config_path, force=True)
    supplied = config.get("audio", {})
    return {
        "input_gain_percent": max(50, min(200, int(supplied.get("input_gain_percent", 135)))),
        "auto_recover": bool(supplied.get("auto_recover", True)),
        "health_interval_seconds": max(3, min(30, int(supplied.get("health_interval_seconds", 5)))),
        "failure_threshold": max(2, min(10, int(supplied.get("failure_threshold", 3)))),
    }


class AudioRecovery:
    """Probe real sample flow and rebuild the complete microphone path."""

    def __init__(
        self,
        *,
        config_path: Path = DEFAULT_CONFIG_PATH,
        status_path: Path = DEFAULT_STATUS_PATH,
        lock_path: Path = DEFAULT_LOCK_PATH,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config_path = Path(config_path).expanduser()
        self.status_path = Path(status_path).expanduser()
        self.lock_path = Path(lock_path).expanduser()
        self.runner = runner
        self.popen = popen
        self.clock = clock

    @staticmethod
    def _pulse_env(server: str) -> dict[str, str]:
        env = os.environ.copy()
        env["PULSE_SERVER"] = server
        return env

    def _run(
        self,
        command: list[str],
        *,
        timeout: float,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess:
        return self.runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            cwd=cwd,
        )

    def _pactl(self, server: str, *args: str, timeout: float = 3) -> subprocess.CompletedProcess:
        return self._run(
            ["pactl", *args],
            timeout=timeout,
            env=self._pulse_env(server),
        )

    def _source_names(self, server: str) -> tuple[set[str], str]:
        try:
            result = self._pactl(server, "list", "short", "sources")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return set(), str(exc)
        if result.returncode:
            return set(), (result.stderr or result.stdout or "pactl falhou").strip()
        names = {
            fields[1]
            for line in result.stdout.splitlines()
            if len(fields := line.split()) >= 2
        }
        return names, ""

    def _probe_source(self, server: str, source: str, timeout: float = 1.2) -> tuple[int, str]:
        command = [
            "parec", f"--device={source}", "--raw", "--rate=16000",
            "--channels=1", "--format=s16le", "--latency-msec=50",
        ]
        process = None
        data = bytearray()
        error = ""
        try:
            process = self.popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._pulse_env(server),
            )
            if process.stdout is None:
                return 0, "parec não abriu a saída de áudio"
            os.set_blocking(process.stdout.fileno(), False)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and len(data) < MIN_STREAM_BYTES:
                if process.poll() is not None:
                    break
                try:
                    chunk = os.read(process.stdout.fileno(), 8192)
                except BlockingIOError:
                    chunk = b""
                if chunk:
                    data.extend(chunk)
                    continue
                time.sleep(0.025)
        except (OSError, subprocess.SubprocessError) as exc:
            error = str(exc)
        finally:
            if process is not None:
                if process.poll() is None:
                    try:
                        process.send_signal(signal.SIGINT)
                        process.wait(timeout=0.7)
                    except (OSError, subprocess.TimeoutExpired):
                        process.kill()
                if process.stderr is not None:
                    try:
                        stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
                        if stderr and not error:
                            error = stderr
                    except (OSError, AttributeError):
                        pass
        return len(data), error

    def _gain_percent(self) -> int | None:
        try:
            result = self._pactl(ISOLATED_SERVER, "get-source-volume", AEC_SOURCE)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode:
            return None
        match = re.search(r"(\d+)%", result.stdout)
        return int(match.group(1)) if match else None

    def health(self, *, probe_audio: bool = True, write_status: bool = False) -> dict[str, Any]:
        upstream_names, upstream_error = self._source_names(UPSTREAM_SERVER)
        aec_names, aec_error = self._source_names(ISOLATED_SERVER)
        upstream_present = UPSTREAM_SOURCE in upstream_names
        aec_present = AEC_SOURCE in aec_names
        upstream_bytes = 0
        aec_bytes = 0
        # Never open a second short-lived capture directly on RDPSource while
        # the AEC tunnel owns it. On WSLg, closing that extra stream can retire
        # the virtual USB/capture endpoint and break the persistent listener.
        # The AEC stream is the end-to-end probe; pactl presence is enough for
        # the upstream control-plane check.
        if probe_audio and aec_present:
            aec_bytes, stream_error = self._probe_source(ISOLATED_SERVER, AEC_SOURCE)
            aec_error = aec_error or stream_error
        upstream_ok = upstream_present
        aec_ok = aec_present and (not probe_audio or aec_bytes >= MIN_STREAM_BYTES)
        healthy = upstream_ok and aec_ok
        if not upstream_present:
            reason = "A fonte RDPSource não está disponível no WSLg"
        elif probe_audio and not upstream_ok:
            reason = "A ponte WSLg não está entregando amostras do microfone"
        elif not aec_present:
            reason = "A fonte NexoAECSource precisa ser reconstruída"
        elif probe_audio and not aec_ok:
            reason = "O cancelamento de eco parou de entregar amostras"
        else:
            reason = "Fluxo de microfone operacional"
        payload = {
            "ok": True,
            "healthy": healthy,
            "reason": reason,
            "probe_mode": "active" if probe_audio else "passive",
            "upstream": {
                "present": upstream_present,
                "flowing": upstream_ok,
                "bytes": upstream_bytes,
                "error": upstream_error,
            },
            "aec": {
                "present": aec_present,
                "flowing": aec_ok,
                "bytes": aec_bytes,
                "error": aec_error,
            },
            "input_gain_percent": self._gain_percent(),
            "checked_at": int(self.clock()),
        }
        if write_status:
            self._write_status(payload)
        return payload

    def _write_status(self, payload: dict[str, Any]) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".audio-health.", dir=str(self.status_path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.status_path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def apply_gain(self, gain_percent: int | None = None) -> dict[str, Any]:
        requested = gain_percent
        if requested is None:
            requested = _audio_settings(self.config_path)["input_gain_percent"]
        requested = max(50, min(200, int(requested)))
        result = self._pactl(
            ISOLATED_SERVER,
            "set-source-volume",
            AEC_SOURCE,
            f"{requested}%",
        )
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "Falha ao aplicar ganho").strip())
        return {"ok": True, "input_gain_percent": requested}

    def _wait_for(self, predicate: Callable[[], bool], timeout: float, label: str) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.35)
        raise RuntimeError(f"{label} não voltou dentro do tempo esperado")

    def _recover_wslg(self) -> None:
        windows_helper = Path(__file__).with_name("windows_wslg_audio.ps1")
        capture = self._run(
            [
                POWERSHELL,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(windows_helper),
                "-Action",
                "capture-status",
            ],
            timeout=12,
            cwd="/mnt/c",
        )
        try:
            capture_state = json.loads((capture.stdout or "").strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError("O Windows não retornou o estado do microfone") from exc
        if capture.returncode or not capture_state.get("available"):
            raise RuntimeError(
                str(capture_state.get("error") or
                    "Aguardando a mesa ou placa de captura voltar no Windows")
            )

        # Release every persistent Pulse client before recycling WSLg. This
        # prevents an old RDP capture stream from racing the new USB endpoint.
        stopped = self._run(
            [
                "systemctl", "--user", "stop",
                "ovos-listener.service", "ovos-audio.service",
                "nexo-pulse-aec.service",
            ],
            timeout=30,
        )
        if stopped.returncode:
            raise RuntimeError(
                (stopped.stderr or stopped.stdout or "Falha ao liberar a cadeia de audio").strip()
            )
        client = self._run(
            [
                POWERSHELL,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(windows_helper),
                "-Action",
                "repair",
            ],
            timeout=20,
            cwd="/mnt/c",
        )
        if client.returncode:
            raise RuntimeError(
                (client.stderr or client.stdout or "Falha ao renovar a sessão de áudio do WSLg").strip()
            )
        # msrdc exists before its dynamic virtual audio channels are ready.
        # Give Weston time to finish that handshake before recreating Pulse.
        time.sleep(8)

        script = r'''
line=$(ps -eo pid=,args= | grep '/usr/bin/pulseaudio' | grep 'module-rdp-sink' | head -n 1)
set -- $line
pid=$1
case "$pid" in ''|*[!0-9]*) echo "WSLg PulseAudio não encontrado" >&2; exit 2;; esac
kill -KILL "$pid"
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  line=$(ps -eo pid=,args= | grep '/usr/bin/pulseaudio' | grep 'module-rdp-sink' | head -n 1)
  set -- $line
  new_pid=$1
  if test -n "$new_pid" && test "$new_pid" != "$pid"; then echo "$new_pid"; exit 0; fi
  sleep 0.5
done
echo "WSLGd não recriou o PulseAudio" >&2
exit 3
'''.strip()
        # Feed the recovery program through stdin.  Passing shell metacharacters
        # through wsl.exe's Windows command-line conversion is not reliable.
        result = self.runner(
            [WSL_EXE, "--system", "sh"],
            input=script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            env=os.environ.copy(),
            cwd="/mnt/c",
        )
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "Falha ao recuperar WSLg").strip())
        # Do not capture RDPSource here. The AEC source tunnel must be its first
        # persistent recording client after the WSLg session renews.
        time.sleep(2)

    def _restart_stack(self) -> None:
        aec = self._run(
            ["systemctl", "--user", "restart", "nexo-pulse-aec.service"],
            timeout=30,
        )
        if aec.returncode:
            raise RuntimeError((aec.stderr or aec.stdout or "Falha ao reiniciar AEC").strip())
        self._wait_for(
            lambda: AEC_SOURCE in self._source_names(ISOLATED_SERVER)[0],
            15,
            "O cancelamento de eco",
        )
        clients = self._run(
            ["systemctl", "--user", "restart", "ovos-audio.service", "ovos-listener.service"],
            timeout=45,
        )
        if clients.returncode:
            raise RuntimeError((clients.stderr or clients.stdout or "Falha ao reiniciar escuta").strip())
        self._wait_for(
            lambda: all(
                self._run(
                    ["systemctl", "--user", "is-active", service], timeout=3
                ).stdout.strip() == "active"
                for service in ("nexo-pulse-aec.service", "ovos-audio.service", "ovos-listener.service")
            ),
            20,
            "A escuta do Nexo",
        )
        # PortAudio/OVOS may restore a cached source level while opening its
        # persistent stream. Apply the configured gain only after that stream
        # is ready so reconnects never leave the microphone at 33%.
        self.apply_gain()

    def repair(self, *, force_wslg: bool = False, reason: str = "manual") -> dict[str, Any]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Uma recuperação do microfone já está em andamento") from exc
            # When the AEC client is absent, probing RDPSource and immediately
            # closing it can make WSLg retire the just-created capture stream.
            # Check presence first and only perform a flow probe while the
            # persistent AEC path already exists.
            before = self.health(probe_audio=False)
            upstream_ready = before["upstream"]["present"]
            recovered_wslg = bool(force_wslg or not upstream_ready)
            if recovered_wslg:
                self._recover_wslg()
            self._restart_stack()
            # The listener is now the persistent consumer. Do not attach a
            # short-lived second recorder after startup: WebRTC treats that as
            # a clock change and can cut the beginning of the next utterance.
            after = self.health(probe_audio=False, write_status=True)
            after.update({
                "repaired": True,
                "recovered_wslg": recovered_wslg,
                "trigger": str(reason)[:80],
            })
            if not after["healthy"]:
                raise RuntimeError(after["reason"])
            self._write_status(after)
            return after

    def watch(self) -> int:
        failures = 0
        last_repair = 0.0
        last_gain: int | None = None
        while True:
            settings = _audio_settings(self.config_path)
            if not settings["auto_recover"]:
                payload = self.health(probe_audio=False)
                payload.update({"watchdog": "disabled", "consecutive_failures": 0})
                self._write_status(payload)
                time.sleep(settings["health_interval_seconds"])
                continue
            # Passive checks are intentional. Opening a recorder every cycle
            # forces module-echo-cancel to resynchronise and fragments speech.
            payload = self.health(probe_audio=False)
            if payload["healthy"]:
                failures = 0
                desired_gain = settings["input_gain_percent"]
                if desired_gain != last_gain or payload.get("input_gain_percent") != desired_gain:
                    try:
                        self.apply_gain(desired_gain)
                        last_gain = desired_gain
                        payload["input_gain_percent"] = desired_gain
                    except Exception as exc:
                        payload["gain_error"] = str(exc)
                payload.update({"watchdog": "monitoring", "consecutive_failures": 0})
                self._write_status(payload)
            else:
                failures += 1
                payload.update({"watchdog": "degraded", "consecutive_failures": failures})
                self._write_status(payload)
                now = time.monotonic()
                if failures >= settings["failure_threshold"] and now - last_repair >= 120:
                    last_repair = now
                    try:
                        repaired = self.repair(reason="watchdog")
                        repaired.update({"watchdog": "recovered", "consecutive_failures": 0})
                        self._write_status(repaired)
                        failures = 0
                    except Exception as exc:
                        error = str(exc)
                        # A powered-off desk is an expected state. Recheck the
                        # Windows endpoint soon, but do not touch WSLg until it
                        # is present again. Keep the long cooldown for genuine
                        # WSLg failures to avoid restart loops.
                        if "Aguardando a mesa" in error or "Nenhum microfone ativo" in error:
                            last_repair = now - 110
                        payload.update({"watchdog": "repair_failed", "repair_error": str(exc)})
                        self._write_status(payload)
            time.sleep(settings["health_interval_seconds"])


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check")
    check.add_argument("--no-probe", action="store_true")
    repair = subparsers.add_parser("repair")
    repair.add_argument("--force-wslg", action="store_true")
    repair.add_argument("--reason", default="manual")
    gain = subparsers.add_parser("apply-gain")
    gain.add_argument("value", nargs="?", type=int)
    subparsers.add_parser("watch")
    args = parser.parse_args(argv)
    recovery = AudioRecovery()
    try:
        if args.command == "check":
            payload = recovery.health(probe_audio=not args.no_probe, write_status=True)
            _print(payload)
            return 0 if payload["healthy"] else 1
        if args.command == "repair":
            _print(recovery.repair(force_wslg=args.force_wslg, reason=args.reason))
            return 0
        if args.command == "apply-gain":
            _print(recovery.apply_gain(args.value))
            return 0
        return recovery.watch()
    except Exception as exc:
        _print({"ok": False, "healthy": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
