#!/usr/bin/env python3
"""Play one bounded Realtime phrase through Nexo's production AEC sink."""

from __future__ import annotations

import json

from nexo_actions.realtime.pulse import PulsePlayback
from nexo_actions.realtime.session import RealtimeSession


def main() -> int:
    audio_bytes = 0
    event_types: list[str] = []
    errors: list[str] = []
    playback = PulsePlayback()

    def on_audio(chunk, _event):
        nonlocal audio_bytes
        audio_bytes += len(chunk)
        playback.write(chunk)

    session = RealtimeSession(
        model="gpt-realtime-2.1",
        voice="marin",
        instructions="Responda em português do Brasil. Diga somente a frase solicitada.",
        automatic_response=False,
        automatic_interruption=False,
        on_audio=on_audio,
        max_reconnects=0,
        connect_timeout=15,
    )
    try:
        playback.begin_response()
        playback.start()
        session.connect()
        for _ in range(8):
            event = session.receive_once()
            event_type = str(event.get("type") or "")
            event_types.append(event_type)
            if event_type in {"session.updated", "error"}:
                break
        if "session.updated" in event_types and "error" not in event_types:
            session.send_text("Diga apenas: Nexo pronto.")
            for _ in range(240):
                event = session.receive_once()
                event_type = str(event.get("type") or "")
                event_types.append(event_type)
                if event_type == "error":
                    errors.append(str((event.get("error") or {}).get("code") or "turn_error"))
                    break
                if event_type == "response.done":
                    break
        drained = playback.wait_until_idle(timeout=8.0)
        playback_error = playback.last_error
        ok = (
            not errors
            and "response.done" in event_types
            and audio_bytes > 0
            and drained
            and playback_error is None
        )
        print(json.dumps({
            "ok": ok,
            "audio_bytes": audio_bytes,
            "response_done": "response.done" in event_types,
            "pulse_drained": drained,
            "timing": playback.diagnostics(),
            "playback_error": type(playback_error).__name__ if playback_error else None,
            "errors": errors,
        }, sort_keys=True))
        return 0 if ok else 1
    finally:
        session.close()
        playback.close()


if __name__ == "__main__":
    raise SystemExit(main())
