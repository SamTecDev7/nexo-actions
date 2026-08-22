#!/usr/bin/env python3
"""Live text-to-audio Realtime roundtrip without microphone or speakers."""

from __future__ import annotations

import json

from nexo_actions.realtime.session import RealtimeSession


def main() -> int:
    audio_bytes = 0
    transcripts: list[str] = []
    events: list[str] = []

    def on_audio(chunk, _event):
        nonlocal audio_bytes
        audio_bytes += len(chunk)

    def on_transcript(text, final, _event):
        if final:
            transcripts.append(str(text))

    session = RealtimeSession(
        model="gpt-realtime-2.1",
        voice="marin",
        instructions="Responda em português do Brasil, de forma extremamente curta.",
        automatic_response=False,
        automatic_interruption=False,
        on_audio=on_audio,
        on_transcript=on_transcript,
        max_reconnects=0,
        connect_timeout=15,
    )
    errors: list[str] = []
    try:
        session.connect()
        for _ in range(8):
            event = session.receive_once()
            event_type = str(event.get("type") or "")
            events.append(event_type)
            if event_type == "session.updated":
                break
            if event_type == "error":
                errors.append(str((event.get("error") or {}).get("message") or "")[:300])
                break
        if not errors:
            session.send_text("Diga apenas: Nexo pronto.")
            for _ in range(240):
                event = session.receive_once()
                event_type = str(event.get("type") or "")
                events.append(event_type)
                if event_type == "error":
                    errors.append(str((event.get("error") or {}).get("message") or "")[:300])
                    break
                if event_type == "response.done":
                    break
        ok = (
            not errors
            and "session.updated" in events
            and "response.done" in events
            and audio_bytes > 0
        )
        print(json.dumps({
            "ok": ok,
            "audio_bytes_received": audio_bytes,
            "audio_timing": session.audio_diagnostics(),
            "response_done": "response.done" in events,
            "final_transcript": transcripts[-1][:160] if transcripts else "",
            "errors": errors,
        }, ensure_ascii=False, sort_keys=True))
        return 0 if ok else 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
