#!/usr/bin/env python3
"""Create and recycle one disposable file to validate the safe delete path."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from nexo_actions.config import windows_to_wsl
from nexo_actions.file_tools import create_text_file, delete_file


def main() -> int:
    filename = f"nexo-recycle-smoke-{uuid.uuid4().hex[:8]}.txt"
    created = create_text_file(
        "downloads", filename, "arquivo temporário do teste Nexo", open_after=False
    )
    path = created["path"]
    deleted = delete_file(path, permanent=False)
    exists_after = windows_to_wsl(path).exists()
    print(json.dumps({
        "created": created,
        "deleted": deleted,
        "exists_after": exists_after,
        "recoverable": True,
    }, ensure_ascii=False, indent=2))
    return 1 if exists_after or not deleted.get("recycled") else 0


if __name__ == "__main__":
    raise SystemExit(main())
