#!/usr/bin/env python3
"""Read-only live smoke test for Nexo visual desktop observation."""

from __future__ import annotations

import base64
import json

from nexo_actions.computer_tools import observe_desktop


def main() -> int:
    result = observe_desktop("active_window", 960, 540)
    encoded = result.pop("image_base64", "")
    image = base64.b64decode(encoded, validate=True)
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("O bridge nao retornou um PNG valido")
    if not result.get("observation_id"):
        raise RuntimeError("A observacao nao retornou observation_id")
    if result.get("image_bytes") != len(image):
        raise RuntimeError("O tamanho declarado da captura diverge do PNG")
    result["image_base64_omitted"] = True
    result["decoded_png_bytes"] = len(image)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
