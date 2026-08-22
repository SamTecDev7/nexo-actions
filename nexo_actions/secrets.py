"""Server-side secret loading for Nexo.

Secrets are deliberately kept outside the user-editable JSON configuration and
are never included in status payloads. The project ``.env`` file is supported
for the current installation, while a process environment variable takes
precedence for service deployments.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def _read_env_value(path: Path, name: str) -> str | None:
    """Read one dotenv value without evaluating shell syntax."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (FileNotFoundError, OSError, UnicodeError):
        return None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            parsed = value.strip()
            if len(parsed) >= 2 and parsed[0] == parsed[-1] and parsed[0] in {'"', "'"}:
                parsed = parsed[1:-1]
            return parsed.strip() or None
    return None


def load_openai_api_key(env_file: Path | None = None) -> str | None:
    """Return the OpenAI API key for internal use only.

    Callers must never place the returned value in logs, exceptions, tool
    results, UI payloads or persisted runtime configuration.
    """
    environment_value = os.environ.get("OPENAI_API_KEY", "").strip()
    if environment_value:
        return environment_value
    return _read_env_value(Path(env_file or DEFAULT_ENV_FILE), "OPENAI_API_KEY")


def openai_credential_status() -> dict:
    """Return a non-sensitive credential status suitable for the ADM UI."""
    environment_value = os.environ.get("OPENAI_API_KEY", "").strip()
    if environment_value:
        return {
            "configured": True,
            "source": "environment",
            "secure_storage": True,
        }

    path = DEFAULT_ENV_FILE
    configured = bool(_read_env_value(path, "OPENAI_API_KEY"))
    secure_storage = False
    if configured:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            secure_storage = mode & 0o077 == 0
        except OSError:
            secure_storage = False
    return {
        "configured": configured,
        "source": "project_env" if configured else "none",
        "secure_storage": secure_storage,
    }
