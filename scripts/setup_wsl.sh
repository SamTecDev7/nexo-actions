#!/usr/bin/env bash
set -euo pipefail

NEXO_REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
NEXO_VENV_PATH="${NEXO_VENV:-$HOME/.venvs/ovos}"
NEXO_PYTHON="$NEXO_VENV_PATH/bin/python"
NEXO_UNIT_DIR="$HOME/.config/systemd/user"
NEXO_UNIT_PATH="$NEXO_UNIT_DIR/nexo-ui.service"

if [[ ! -x "$NEXO_PYTHON" ]]; then
  echo "Ambiente OVOS não encontrado em $NEXO_VENV_PATH" >&2
  exit 1
fi

"$NEXO_PYTHON" -m pip install --disable-pip-version-check -e "$NEXO_REPO_ROOT"
"$NEXO_PYTHON" -c 'from nexo_actions.config import ensure_config; print(ensure_config())'
"$NEXO_PYTHON" "$NEXO_REPO_ROOT/scripts/configure_audio.py"
bash "$NEXO_REPO_ROOT/scripts/install_pulse_aec.sh"

mkdir -p "$NEXO_UNIT_DIR"
NEXO_UNIT_TEMP="$(mktemp)"
trap 'rm -f "$NEXO_UNIT_TEMP"' EXIT
cat >"$NEXO_UNIT_TEMP" <<EOF
[Unit]
Description=Nexo Holographic Interface
After=network.target

[Service]
Type=simple
WorkingDirectory=$NEXO_REPO_ROOT
ExecStart=$NEXO_PYTHON -m nexo_actions.ui.server --host 127.0.0.1 --port 8787
Restart=on-failure
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
install -m 0644 "$NEXO_UNIT_TEMP" "$NEXO_UNIT_PATH"

systemctl --user daemon-reload
systemctl --user enable --now nexo-ui.service

"$NEXO_PYTHON" "$NEXO_REPO_ROOT/scripts/apply_openai_profile.py" \
  --if-configured --initialize-only --recommended

if systemctl --user is-active --quiet ovos-listener.service; then
  systemctl --user restart ovos-listener.service
fi
if systemctl --user is-active --quiet ovos-core.service; then
  systemctl --user restart ovos-core.service
fi

echo "Nexo instalado no ambiente OVOS."
echo "Interface: http://127.0.0.1:8787"
echo "Configuração: $HOME/.config/nexo/config.json"
