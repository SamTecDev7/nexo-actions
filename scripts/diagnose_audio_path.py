#!/usr/bin/env python3
"""Play and digitally capture a Nexo phrase through the complete WSLg path."""

from __future__ import annotations

import argparse
import array
import io
import json
import math
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path


DEFAULT_TEXT = (
    "Este é um teste técnico da voz do Nexo pela saída real. "
    "A frase deve tocar de forma contínua, clara e sem pequenos cortes."
)


def _synthesize(text: str, voice: str, endpoint: str) -> bytes:
    query = urllib.parse.urlencode({"utterance": text, "voice": voice})
    with urllib.request.urlopen(f"{endpoint}?{query}", timeout=90) as response:
        if response.status != 200:
            raise RuntimeError(f"TTS respondeu HTTP {response.status}")
        return response.read()


def _capture_playback(wav_path: Path, raw_path: Path) -> None:
    capture_env = os.environ.copy()
    capture_env["PULSE_SERVER"] = "unix:/mnt/wslg/PulseServer"
    playback_env = os.environ.copy()
    playback_env["PULSE_SERVER"] = "unix:/tmp/nexo-pulse/native"

    with raw_path.open("wb") as raw_output:
        capture = subprocess.Popen(
            [
                "parec",
                "--device=RDPSink.monitor",
                "--raw",
                "--format=s16le",
                "--rate=44100",
                "--channels=2",
            ],
            stdout=raw_output,
            stderr=subprocess.PIPE,
            env=capture_env,
        )
        try:
            time.sleep(0.7)
            subprocess.run(
                ["paplay", "--device=NexoAECSink", str(wav_path)],
                check=True,
                env=playback_env,
                timeout=90,
            )
            time.sleep(0.7)
        finally:
            capture.terminate()
            try:
                capture.wait(timeout=3)
            except subprocess.TimeoutExpired:
                capture.kill()
                capture.wait(timeout=3)
        if capture.returncode not in {-15, 0}:
            error = (capture.stderr.read() if capture.stderr else b"").decode(
                "utf-8", errors="replace"
            )
            raise RuntimeError(f"Falha ao capturar RDPSink.monitor: {error.strip()}")


def _rms(samples: list[int]) -> float:
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def _signal_stats(mono: list[int], rate: int) -> dict[str, float | int]:
    window_size = max(1, rate // 100)  # 10 ms
    windows = [
        _rms(mono[index : index + window_size])
        for index in range(0, len(mono) - window_size + 1, window_size)
    ]
    active = [index for index, value in enumerate(windows) if value > 80]
    if not active:
        raise RuntimeError("A captura contém somente silêncio")
    region = windows[active[0] : active[-1] + 1]
    return {
        "active_seconds": len(region) * 0.01,
        "digital_gaps": sum(value < 8 for value in region),
        "very_low_windows": sum(value < 40 for value in region),
        "peak": max(abs(sample) for sample in mono),
    }


def _analyze(wav_path: Path, raw_path: Path) -> dict[str, float | int | bool]:
    with wave.open(str(wav_path), "rb") as source:
        input_duration = source.getnframes() / source.getframerate()
        input_rate = source.getframerate()
        input_channels = source.getnchannels()
        input_width = source.getsampwidth()
        input_pcm = source.readframes(source.getnframes())
    if input_width != 2:
        raise RuntimeError(f"WAV de entrada usa {input_width * 8} bits; esperado: 16")
    input_samples = array.array("h")
    input_samples.frombytes(input_pcm)
    if os.sys.byteorder != "little":
        input_samples.byteswap()
    if input_channels == 1:
        input_mono = list(input_samples)
    else:
        input_mono = [
            sum(input_samples[index : index + input_channels]) // input_channels
            for index in range(0, len(input_samples) - input_channels + 1, input_channels)
        ]
    input_stats = _signal_stats(input_mono, input_rate)

    stereo = array.array("h")
    stereo.frombytes(raw_path.read_bytes())
    if not stereo:
        raise RuntimeError("O monitor da saída não capturou áudio")
    if os.sys.byteorder != "little":
        stereo.byteswap()
    mono = [(stereo[index] + stereo[index + 1]) // 2 for index in range(0, len(stereo) - 1, 2)]
    output_stats = _signal_stats(mono, 44100)
    duration_error = abs(output_stats["active_seconds"] - input_stats["active_seconds"])
    extra_gaps = max(0, output_stats["digital_gaps"] - input_stats["digital_gaps"])
    return {
        "ok": extra_gaps == 0 and duration_error < 0.08,
        "input_duration_seconds": round(input_duration, 3),
        "input_active_seconds": round(input_stats["active_seconds"], 3),
        "captured_active_seconds": round(output_stats["active_seconds"], 3),
        "duration_error_seconds": round(duration_error, 3),
        "input_digital_gaps_10ms": input_stats["digital_gaps"],
        "captured_digital_gaps_10ms": output_stats["digital_gaps"],
        "extra_digital_gaps_10ms": extra_gaps,
        "input_very_low_windows_10ms": input_stats["very_low_windows"],
        "captured_very_low_windows_10ms": output_stats["very_low_windows"],
        "capture_peak": output_stats["peak"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--voice", default="neural:ptbr-natural")
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:8791/v2/synthesize",
    )
    args = parser.parse_args()

    audio = _synthesize(args.text, args.voice, args.endpoint)
    with tempfile.TemporaryDirectory(prefix="nexo-audio-diagnostic-") as directory:
        wav_path = Path(directory) / "input.wav"
        raw_path = Path(directory) / "output.raw"
        wav_path.write_bytes(audio)
        _capture_playback(wav_path, raw_path)
        result = _analyze(wav_path, raw_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
