#!/usr/bin/env python3
"""Validate Nexo's OpenAI credential without ever printing the secret."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_env_value(path: Path, name: str) -> str:
    if not path.is_file():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip().strip('"\'')
    return ""


def validate(env_file: Path, timeout: float = 12.0) -> dict[str, object]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    source = "environment"
    if not api_key:
        api_key = load_env_value(env_file, "OPENAI_API_KEY")
        source = str(env_file)
    if len(api_key) < 20:
        return {"ok": False, "status": "missing_or_invalid_format", "source": source}

    request = urllib.request.Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_code = "http_error"
        try:
            body = json.loads(exc.read().decode("utf-8"))
            error_code = str(body.get("error", {}).get("code") or body.get("error", {}).get("type") or error_code)
        except Exception:
            pass
        return {"ok": False, "status": error_code, "http_status": exc.code, "source": source}
    except (OSError, TimeoutError, ValueError) as exc:
        return {"ok": False, "status": type(exc).__name__, "source": source}

    models = {
        str(item.get("id") or "")
        for item in payload.get("data", [])
        if isinstance(item, dict)
    }
    return {
        "ok": True,
        "status": "authorized",
        "source": source,
        "models_visible": len(models),
        "gpt_5_6_sol_visible": "gpt-5.6-sol" in models or "gpt-5.6" in models,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    args = parser.parse_args()
    result = validate(args.env_file.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
