#!/usr/bin/env bash
set -euo pipefail

NEXO_REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
NEXO_VENV_PATH="${NEXO_VENV:-$HOME/.venvs/ovos}"
exec "$NEXO_VENV_PATH/bin/python" "$NEXO_REPO_ROOT/scripts/check_environment.py"
