#!/usr/bin/env python3
"""Non-destructive desktop/files/browser integration test with cleanup."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

from nexo_actions.browser_tools import open_website
from nexo_actions.config import known_directories, windows_to_wsl
from nexo_actions.desktop_tools import bridge_health, list_windows, window_action
from nexo_actions.file_tools import create_text_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--open-site", action="store_true")
    args = parser.parse_args()

    filename = f"nexo-integration-{uuid.uuid4().hex[:8]}.txt"
    target = windows_to_wsl(known_directories()["downloads"]) / filename
    report = {"health": bridge_health(), "file": None, "window": None, "site": None}
    try:
        report["file"] = create_text_file(
            "downloads",
            filename,
            "teste de integração do Nexo",
            open_after=True,
        )
        time.sleep(2)
        matches = [
            item for item in list_windows()["windows"]
            if filename.casefold() in item["title"].casefold()
        ]
        if not matches:
            raise RuntimeError("A janela do arquivo de teste não apareceu")
        report["window"] = {
            "found": matches[0]["title"],
            "minimize": window_action("minimize", filename)["ok"],
            "restore": window_action("restore", filename)["ok"],
            "close": window_action("close", filename)["ok"],
        }
        if args.open_site:
            report["site"] = open_website("club envios")
        time.sleep(1)
    finally:
        try:
            if target.exists():
                target.unlink()
        except OSError as exc:
            report["cleanup_error"] = str(exc)

    report["cleaned_up"] = not Path(target).exists()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
