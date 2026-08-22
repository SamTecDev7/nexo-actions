#!/usr/bin/env bash
set -euo pipefail

NEXO_REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
NEXO_VENV_PATH="${NEXO_VENV:-$HOME/.venvs/ovos}"
NEXO_PYTHON="$NEXO_VENV_PATH/bin/python"

if [[ ! -x "$NEXO_PYTHON" ]]; then
  echo "Ambiente OVOS não encontrado em $NEXO_VENV_PATH" >&2
  exit 1
fi

"$NEXO_PYTHON" -m pip install --disable-pip-version-check -e "$NEXO_REPO_ROOT"

if [[ "${1:-}" != "--no-restart" ]]; then
  systemctl --user restart ovos-core.service
fi

echo "Skill Nexo instalada em modo editável."
