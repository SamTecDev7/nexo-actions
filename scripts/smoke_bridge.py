#!/usr/bin/env python3
"""Read-only integration smoke test for the Windows companion."""

from __future__ import annotations

import json
import argparse

from nexo_actions.desktop_tools import active_window, bridge_health, list_windows, window_action


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--close")
    args = parser.parse_args()
    if args.close:
        print(json.dumps(window_action("close", args.close), ensure_ascii=False, indent=2))
        return 0
    health = bridge_health()
    active = active_window().get("window", {})
    windows = list_windows()
    print(json.dumps({
        "health": health,
        "active": {
            "process": active.get("process"),
            "title_chars": len(str(active.get("title") or "")),
        },
        "window_count": windows["count"],
        "sample_processes": sorted({
            str(item.get("process") or "")
            for item in windows["windows"]
            if item.get("process")
        })[:8],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
