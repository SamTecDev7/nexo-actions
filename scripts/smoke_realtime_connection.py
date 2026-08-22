#!/usr/bin/env python3
"""Validate a quiet OpenAI Realtime session without microphone or speakers."""

from __future__ import annotations

import argparse
import json
import time

from nexo_actions.realtime.session import RealtimeSession


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=0,
        help="Keep the quiet connection open, then verify it still accepts a request.",
    )
    parser.add_argument(
        "--transcription-model",
        default="gpt-transcribe",
        help="Input transcription model advertised in the Realtime session.",
    )
    arguments = parser.parse_args()
    events: list[str] = []
    errors: list[dict[str, str]] = []
    session = RealtimeSession(
        model="gpt-realtime-2.1",
        voice="marin",
        instructions="Teste técnico silencioso. Não crie resposta.",
        input_transcription_model=arguments.transcription_model,
        automatic_response=False,
        automatic_interruption=False,
        max_reconnects=0,
        connect_timeout=12,
    )
    try:
        session.connect()
        for _ in range(6):
            event = session.receive_once()
            event_type = str(event.get("type") or "")
            events.append(event_type)
            if event_type == "error":
                raw_error = event.get("error") if isinstance(event.get("error"), dict) else {}
                errors.append({
                    "type": str(raw_error.get("type") or "")[:80],
                    "code": str(raw_error.get("code") or "")[:80],
                    "param": str(raw_error.get("param") or "")[:120],
                    "message": str(raw_error.get("message") or "")[:300].replace("sk-", "<redacted>-"),
                })
            if event_type == "session.updated":
                break
            if event_type == "error":
                break
        idle_roundtrip = not arguments.idle_seconds
        if "session.updated" in events and arguments.idle_seconds > 0:
            time.sleep(arguments.idle_seconds)
            session.send_text("Diga apenas: conexão ativa.")
            for _ in range(240):
                event = session.receive_once()
                event_type = str(event.get("type") or "")
                events.append(event_type)
                if event_type in {"error", "response.done"}:
                    break
            idle_roundtrip = "response.done" in events and "error" not in events
        ok = (
            "session.updated" in events
            and "error" not in events
            and idle_roundtrip
        )
        print(json.dumps({
            "ok": ok,
            "model": session.model,
            "voice": session.voice,
            "transcription_model": arguments.transcription_model,
            "session_updated": "session.updated" in events,
            "idle_seconds": arguments.idle_seconds,
            "idle_roundtrip": idle_roundtrip,
            "event_types": events,
            "errors": errors,
        }, ensure_ascii=False, sort_keys=True))
        return 0 if ok else 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
