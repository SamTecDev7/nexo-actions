#!/usr/bin/env python3
"""Generate the selected OpenAI listening cue in memory without playback."""

from __future__ import annotations

import io
import json
import wave

from nexo_actions.admin import AdminService, _trim_listening_prompt


def main() -> int:
    service = AdminService()
    try:
        audio = _trim_listening_prompt(
            service._render_openai_listening_prompt("Manda.", "marin")
        )
        with wave.open(io.BytesIO(audio), "rb") as stream:
            rate = stream.getframerate()
            frames = stream.getnframes()
            channels = stream.getnchannels()
        duration_ms = round(frames / rate * 1000)
        ok = rate > 0 and 150 <= duration_ms <= 3_000 and channels in {1, 2}
        print(json.dumps({
            "ok": ok,
            "voice": "marin",
            "wav_bytes": len(audio),
            "sample_rate": rate,
            "channels": channels,
            "duration_ms": duration_ms,
        }, ensure_ascii=False, sort_keys=True))
        return 0 if ok else 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
