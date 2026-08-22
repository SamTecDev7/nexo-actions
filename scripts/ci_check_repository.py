#!/usr/bin/env python3
"""Fail CI if tracked repository files contain likely private credentials."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_NAMES = {".env.example"}
FORBIDDEN_NAME = re.compile(
    r"(^|/)(?:\.env(?:\..+)?|id_rsa|id_ed25519|credentials?\.json|"
    r"secrets?\.(?:json|ya?ml)|[^/]+\.(?:key|pem|p12|pfx))$",
    re.I,
)
SECRET_VALUE = re.compile(
    rb"(?:sk-[A-Za-z0-9_-]{40,}|github_pat_[A-Za-z0-9_]{50,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})"
)
MAX_SCAN_BYTES = 4 * 1024 * 1024


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        root / item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]


def validate(paths: list[Path], root: Path = ROOT) -> None:
    violations: list[str] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if path.name not in ALLOWED_NAMES and FORBIDDEN_NAME.search(relative):
            violations.append(f"nome sensível rastreado: {relative}")
            continue
        if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
            continue
        payload = path.read_bytes()
        if b"\0" not in payload[:8192] and SECRET_VALUE.search(payload):
            violations.append(f"possível credencial em: {relative}")
    if violations:
        raise ValueError("\n".join(violations))


def main() -> int:
    try:
        validate(_tracked_files(ROOT))
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SystemExit(f"não foi possível enumerar os arquivos rastreados: {exc}") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print("repository_credentials=clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
