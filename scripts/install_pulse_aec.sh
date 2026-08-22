#!/usr/bin/env bash
set -euo pipefail

NEXO_REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
NEXO_RUNTIME_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/nexo-pulse/root"
NEXO_VENV_PATH="${NEXO_VENV:-$HOME/.venvs/ovos}"
NEXO_UNIT_DIR="$HOME/.config/systemd/user"
NEXO_TEMP_DIR="$(mktemp -d --tmpdir nexo-pulse.XXXXXX)"

cleanup() {
  case "$NEXO_TEMP_DIR" in
    /tmp/nexo-pulse.*) rm -rf -- "$NEXO_TEMP_DIR" ;;
    *) echo "Diretorio temporario inesperado; preservando: $NEXO_TEMP_DIR" >&2 ;;
  esac
}
trap cleanup EXIT

mkdir -p "$NEXO_RUNTIME_ROOT" "$NEXO_UNIT_DIR"
(
  cd "$NEXO_TEMP_DIR"
  if apt-cache show liborc-0.4-0t64 >/dev/null 2>&1; then
    NEXO_ORC_PACKAGE="liborc-0.4-0t64"
  elif apt-cache show liborc-0.4-0 >/dev/null 2>&1; then
    NEXO_ORC_PACKAGE="liborc-0.4-0"
  else
    echo "Nenhum pacote liborc compatível foi encontrado nesta versão do Ubuntu." >&2
    exit 1
  fi
  apt download pulseaudio libtdb1 "$NEXO_ORC_PACKAGE" libwrap0
  for package in ./*.deb; do
    dpkg-deb -x "$package" "$NEXO_RUNTIME_ROOT"
  done
)

NEXO_AEC_UNIT_TEMP="$NEXO_TEMP_DIR/nexo-pulse-aec.service"
NEXO_WATCHDOG_UNIT_TEMP="$NEXO_TEMP_DIR/nexo-audio-watchdog.service"
sed "s|%h/nexo-actions|$NEXO_REPO_ROOT|g" \
  "$NEXO_REPO_ROOT/systemd/nexo-pulse-aec.service" >"$NEXO_AEC_UNIT_TEMP"
sed -e "s|%h/nexo-actions|$NEXO_REPO_ROOT|g" \
    -e "s|%h/.venvs/ovos|$NEXO_VENV_PATH|g" \
  "$NEXO_REPO_ROOT/systemd/nexo-audio-watchdog.service" >"$NEXO_WATCHDOG_UNIT_TEMP"
install -m 0644 "$NEXO_AEC_UNIT_TEMP" "$NEXO_UNIT_DIR/nexo-pulse-aec.service"
install -m 0644 "$NEXO_WATCHDOG_UNIT_TEMP" "$NEXO_UNIT_DIR/nexo-audio-watchdog.service"

systemctl --user daemon-reload
systemctl --user enable nexo-pulse-aec.service
systemctl --user enable nexo-audio-watchdog.service
systemctl --user restart nexo-pulse-aec.service

NEXO_AEC_SOURCES=""
NEXO_AEC_SINKS=""
for _attempt in {1..50}; do
  NEXO_AEC_SOURCES="$(PULSE_SERVER=unix:/tmp/nexo-pulse/native pactl list short sources 2>/dev/null || true)"
  NEXO_AEC_SINKS="$(PULSE_SERVER=unix:/tmp/nexo-pulse/native pactl list short sinks 2>/dev/null || true)"
  if [[ "$NEXO_AEC_SOURCES" == *NexoAECSource* && "$NEXO_AEC_SINKS" == *NexoAECSink* ]]; then
    break
  fi
  sleep 0.2
done
grep -q NexoAECSource <<<"$NEXO_AEC_SOURCES"
grep -q NexoAECSink <<<"$NEXO_AEC_SINKS"

for unit in ovos-audio.service ovos-listener.service; do
  dropin="$NEXO_UNIT_DIR/$unit.d"
  mkdir -p "$dropin"
  install -m 0644 \
    "$NEXO_REPO_ROOT/systemd/ovos-pulse-aec.conf" \
    "$dropin/zz-nexo-pulse-aec.conf"
  # Superseded filename from early Nexo AEC builds.  The exact target is
  # confined to this service's drop-in directory.
  rm -f -- "$dropin/20-nexo-pulse-aec.conf"
done

systemctl --user daemon-reload
systemctl --user restart ovos-audio.service
systemctl --user restart ovos-listener.service
systemctl --user restart nexo-audio-watchdog.service

echo "Nexo WebRTC AEC instalado: NexoAECSource -> NexoAECSink"
