"""stdin/stdout proxy that lets WSL reach the Windows loopback bridge."""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--token-file")
    args = parser.parse_args()
    token_path = Path(args.token_file) if args.token_file else Path(__file__).resolve().parents[1] / "bridge-token"
    token = token_path.read_text(encoding="utf-8").strip()
    body = sys.stdin.buffer.read(65537)
    if not body or len(body) > 65536:
        raise ValueError("invalid request body")
    request = urllib.request.Request(
        args.url.rstrip("/") + "/rpc",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            sys.stdout.buffer.write(response.read())
    except urllib.error.HTTPError as exc:
        # RPC/application errors are valid JSON responses, not transport failures.
        sys.stdout.buffer.write(exc.read())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
