#!/usr/bin/env python3
"""Synthetic speech smoke for Nexo's high-accuracy Realtime transcription."""

from __future__ import annotations

import io
import json
import threading
import time
import unicodedata
import wave

from nexo_actions.admin import AdminService
from nexo_actions.realtime.coordinator import REALTIME_TRANSCRIPTION_CONTEXT
from nexo_actions.realtime.session import RealtimeSession, SessionState


PHRASE = (
    "Nexo, abra o Codex e o projeto Wave Chat. "
    "Depois pesquise Clube Envios em Goiânia."
)


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    return " ".join(
        "".join(char for char in text if not unicodedata.combining(char))
        .casefold()
        .split()
    )


def main() -> int:
    admin = AdminService()
    try:
        wav_audio = admin._render_openai_listening_prompt(PHRASE, "marin")
    finally:
        admin.close()
    with wave.open(io.BytesIO(wav_audio), "rb") as stream:
        properties = {
            "channels": stream.getnchannels(),
            "sample_width": stream.getsampwidth(),
            "sample_rate": stream.getframerate(),
        }
        pcm = stream.readframes(stream.getnframes())
    if properties != {"channels": 1, "sample_width": 2, "sample_rate": 24_000}:
        print(json.dumps({"ok": False, "reason": "unexpected_wav", **properties}))
        return 1

    connected = threading.Event()
    completed = threading.Event()
    transcripts: list[str] = []
    errors: list[str] = []
    event_types: list[str] = []

    def on_state(state):
        if state == SessionState.CONNECTED:
            connected.set()

    def on_transcript(text, final, event):
        if (
            final
            and str(event.get("type") or "").startswith(
                "conversation.item.input_audio_transcription"
            )
        ):
            transcripts.append(str(text))
            completed.set()

    def on_error(error):
        errors.append(type(error).__name__)
        completed.set()

    def on_event(event):
        event_types.append(str(event.get("type") or ""))

    session = RealtimeSession(
        model="gpt-realtime-2.1",
        voice="marin",
        instructions="Teste técnico silencioso. Não responda.",
        input_transcription_model="gpt-transcribe",
        input_transcription_prompt=REALTIME_TRANSCRIPTION_CONTEXT,
        automatic_response=False,
        automatic_interruption=False,
        on_state_change=on_state,
        on_transcript=on_transcript,
        on_event=on_event,
        on_error=on_error,
        max_reconnects=0,
        connect_timeout=15,
    )
    try:
        session.start()
        if not connected.wait(15):
            print(json.dumps({"ok": False, "reason": "connect_timeout"}))
            return 1
        chunk_bytes = 4_800
        for index in range(0, len(pcm), chunk_bytes):
            session.send_audio(pcm[index:index + chunk_bytes])
            time.sleep(0.04)
        silence = bytes(24_000 * 2)
        for index in range(0, len(silence), chunk_bytes):
            session.send_audio(silence[index:index + chunk_bytes])
            time.sleep(0.04)
        # Commit explicitly so this synthetic/non-realtime feed does not rely
        # on wall-clock VAD timing. Production microphone turns still use VAD.
        session.send_event({"type": "input_audio_buffer.commit"})
        completed.wait(20)
    finally:
        session.close()

    transcript = transcripts[-1] if transcripts else ""
    normalized = _normalized(transcript)
    expected = {
        "nexo": "nexo" in normalized,
        "codex": "codex" in normalized,
        "wave_chat": "wave chat" in normalized or "wavechat" in normalized,
        "clube_envios": "clube envios" in normalized,
        "goiania": "goiania" in normalized,
    }
    ok = not errors and all(expected.values())
    print(json.dumps({
        "ok": ok,
        "model": "gpt-transcribe",
        "recognized": expected,
        "transcript": transcript[:240],
        "errors": errors,
        "event_types": event_types[-24:],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
