#!/usr/bin/env python3
"""Small messagebus helper for safe Nexo development and diagnostics."""

from __future__ import annotations

import argparse
import json
import threading
import time

from ovos_bus_client.client import MessageBusClient
from ovos_bus_client.message import Message


SKILL_ID = "nexo-actions.samtec"


def _connect() -> MessageBusClient:
    bus = MessageBusClient()
    bus.run_in_thread()
    if not bus.connected_event.wait(5):
        raise RuntimeError("Não foi possível conectar ao messagebus do OVOS")
    return bus


def reload_skill(bus: MessageBusClient, pause: float) -> None:
    """Reload Nexo's Python code without restarting the listener and STT."""
    bus.emit(Message(
        "mycroft.skills.reload",
        {"skill_id": SKILL_ID},
        {"source": "nexo-bus-helper"},
    ))
    time.sleep(pause)


def emit_utterance(bus: MessageBusClient, text: str) -> None:
    bus.emit(
        Message(
            "recognizer_loop:utterance",
            {"utterance": text, "utterances": [text], "lang": "pt-BR"},
            {"source": "nexo-bus-helper", "destination": "skills"},
        )
    )
    time.sleep(0.5)


def emit_wake_utterance(bus: MessageBusClient, text: str) -> None:
    """Exercise the same wake-word reservation used by the live listener."""
    bus.emit(
        Message(
            "recognizer_loop:wakeword",
            {"utterance": "nexo", "key_phrase": "nexo"},
            {"source": "nexo-bus-helper"},
        )
    )
    time.sleep(0.2)
    emit_utterance(bus, text)


def emit_speak(bus: MessageBusClient, text: str) -> None:
    """Send a short diagnostic phrase through the real OVOS TTS service."""
    bus.emit(
        Message(
            "speak",
            {"utterance": text, "lang": "pt-BR"},
            {"source": "nexo-bus-helper", "skill_id": SKILL_ID},
        )
    )
    time.sleep(0.5)


def emit_ui_state(bus: MessageBusClient, state: str, detail: str = "") -> None:
    bus.emit(Message(
        "nexo.ui.state",
        {"state": state, "detail": detail},
        {"source": "nexo-bus-helper"},
    ))
    time.sleep(0.25)


def smoke_conversation(bus: MessageBusClient) -> None:
    """Exercise native intent, converse routing, TTS and listener restore."""
    watched = (
        "recognizer_loop:utterance",
        "recognizer_loop:state.set",
        "recognizer_loop:audio_output_start",
        "recognizer_loop:audio_output_end",
        "intent.service.skills.activate",
        "intent.service.skills.deactivate",
        "speak",
    )
    events = []
    lock = threading.Lock()
    started = time.monotonic()

    def record(message):
        with lock:
            events.append(
                {
                    "after_seconds": round(time.monotonic() - started, 3),
                    "type": message.msg_type,
                    "data": message.data,
                    "context": message.context,
                }
            )

    for event in watched:
        bus.on(event, record)

    emit_utterance(bus, "vamos conversar")
    time.sleep(7)
    emit_utterance(bus, "como está minha GPU?")
    time.sleep(15)
    emit_utterance(bus, "pode parar")
    time.sleep(6)

    with lock:
        captured = list(events)
    print(json.dumps(captured, ensure_ascii=False, indent=2, default=str))


def smoke_barge_in(bus: MessageBusClient) -> None:
    """Prove that only an utterance beginning with Nexo stops speech."""
    events = []
    lock = threading.Lock()
    started = time.monotonic()

    def record(message):
        with lock:
            events.append(
                {
                    "after_seconds": round(time.monotonic() - started, 3),
                    "type": message.msg_type,
                    "reason": message.data.get("reason"),
                }
            )

    watched = (
        "recognizer_loop:audio_output_start",
        "recognizer_loop:audio_output_end",
        "mycroft.audio.speech.stop",
    )
    for event in watched:
        bus.on(event, record)

    emit_utterance(bus, "vamos conversar")
    time.sleep(3)
    bus.emit(Message(
        "recognizer_loop:audio_output_start",
        {"test": True},
        {"skill_id": SKILL_ID, "source": "nexo-bus-helper"},
    ))
    time.sleep(0.25)
    bus.emit(
        Message(
            "recognizer_loop:record_end",
            {"test": True},
            {"source": "audio"},
        )
    )
    time.sleep(0.25)
    with lock:
        premature = [event for event in events if event["type"] == "mycroft.audio.speech.stop"]
    if premature:
        raise RuntimeError("TTS was stopped before STT confirmed a human utterance")

    bus.emit(Message(
        "recognizer_loop:utterance",
        {
            "utterance": "mas o que é esse termo",
            "utterances": ["mas o que é esse termo"],
            "lang": "pt-BR",
        },
        {"source": "audio", "destination": "skills"},
    ))
    time.sleep(1)
    with lock:
        background_stops = [
            event for event in events
            if event["type"] == "mycroft.audio.speech.stop"
        ]
    if background_stops:
        raise RuntimeError("Background speech interrupted TTS without the Nexo wake word")

    bus.emit(Message(
        "recognizer_loop:utterance",
        {
            "utterance": "Nexo, pode parar",
            "utterances": ["Nexo, pode parar"],
            "lang": "pt-BR",
        },
        {"source": "audio", "destination": "skills"},
    ))
    time.sleep(4)
    with lock:
        captured = list(events)
    confirmed = [
        event for event in captured
        if event["type"] == "mycroft.audio.speech.stop"
        and event["reason"] == "confirmed_human_barge_in"
    ]
    if not confirmed:
        raise RuntimeError("Confirmed human barge-in did not stop speech")
    print(json.dumps(captured, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    reload_parser = subparsers.add_parser("reload")
    reload_parser.add_argument("--pause", type=float, default=2.0)

    say_parser = subparsers.add_parser("utterance")
    say_parser.add_argument("text")

    wake_parser = subparsers.add_parser("wake-utterance")
    wake_parser.add_argument("text")

    speak_parser = subparsers.add_parser("speak")
    speak_parser.add_argument("text")

    state_parser = subparsers.add_parser("state")
    state_parser.add_argument(
        "value",
        choices=("idle", "listening", "thinking", "speaking", "acting", "confirming", "error"),
    )
    state_parser.add_argument("--detail", default="")

    subparsers.add_parser("smoke-conversation")
    subparsers.add_parser("smoke-barge-in")

    args = parser.parse_args()
    bus = _connect()
    try:
        if args.command == "reload":
            reload_skill(bus, args.pause)
        elif args.command == "smoke-conversation":
            smoke_conversation(bus)
        elif args.command == "smoke-barge-in":
            smoke_barge_in(bus)
        elif args.command == "state":
            emit_ui_state(bus, args.value, args.detail)
        elif args.command == "speak":
            emit_speak(bus, args.text)
        elif args.command == "wake-utterance":
            emit_wake_utterance(bus, args.text)
        else:
            emit_utterance(bus, args.text)
    finally:
        bus.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
