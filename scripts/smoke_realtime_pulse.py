#!/usr/bin/env python3
"""Verify Realtime playback reaches Nexo's isolated Pulse server.

The probe sends only silence. It intentionally does not open a second capture
stream because doing so can force WebRTC AEC to resynchronise a live listener.
"""

from __future__ import annotations

import json
import time

from nexo_actions.realtime.pulse import PulsePlayback


def main() -> int:
    player = PulsePlayback(prebuffer_msec=20)
    try:
        player.begin_response()
        # 200 ms of PCM16 mono silence at the Realtime API's 24 kHz format.
        player.write(bytes(24_000 * 2 // 5))
        deadline = time.monotonic() + 1
        while not player.running and player.last_error is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        drained = player.wait_until_idle(timeout=2.0)
        payload = {
            "ok": player.running and player.last_error is None and drained,
            "running": player.running,
            "drained": drained,
            "queued_bytes": player.queued_bytes,
            "timing": player.diagnostics(),
            "pulse_server": player.pulse_server,
            "error_type": (
                type(player.last_error).__name__ if player.last_error else None
            ),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["ok"] else 1
    finally:
        player.close()


if __name__ == "__main__":
    raise SystemExit(main())
