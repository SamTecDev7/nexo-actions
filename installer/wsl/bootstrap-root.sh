#!/usr/bin/env bash
set -euo pipefail

# Runs as root inside the selected WSL distribution.  It contains no API key
# handling; the only optional stdin value is the password for a newly-created
# Linux user.

NEXO_USER=""
PASSWORD_FROM_STDIN=0

while (($#)); do
  case "$1" in
    --user)
      NEXO_USER="${2:-}"
      shift 2
      ;;
    --password-stdin)
      PASSWORD_FROM_STDIN=1
      shift
      ;;
    *)
      echo "Argumento desconhecido: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! "$NEXO_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  echo "Usuário WSL inválido." >&2
  exit 2
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Não foi possível identificar a distribuição Linux." >&2
  exit 2
fi
# shellcheck disable=SC1091
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || ! "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]]; then
  echo "O Nexo suporta Ubuntu 22.04 ou 24.04 no WSL; detectado: ${ID:-desconhecido} ${VERSION_ID:-desconhecido}." >&2
  exit 2
fi

if ! command -v flock >/dev/null 2>&1; then
  echo "flock não está disponível para proteger o bootstrap concorrente." >&2
  exit 2
fi
exec 9>"/var/lock/nexo-bootstrap.lock"
if ! flock -n 9; then
  echo "Outro bootstrap do Nexo já está em andamento nesta distribuição." >&2
  exit 4
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y --no-install-recommends \
  bash ca-certificates curl ffmpeg git jq \
  build-essential python3 python3-dev python3-pip python3-venv \
  libasound2-dev libffi-dev libopenblas-dev libsndfile1 \
  libssl-dev portaudio19-dev pulseaudio-utils sudo util-linux

python3 - <<'PY'
import sys

if not ((3, 10) <= sys.version_info[:2] < (3, 13)):
    raise SystemExit(
        f"O Nexo requer Python 3.10, 3.11 ou 3.12; detectado {sys.version.split()[0]}"
    )
PY

CREATED_USER=0
if ! id -u "$NEXO_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --groups audio,sudo "$NEXO_USER"
  CREATED_USER=1
fi

if ((PASSWORD_FROM_STDIN)); then
  IFS= read -r NEXO_PASSWORD || true
  if [[ -z "${NEXO_PASSWORD:-}" ]]; then
    echo "A senha do novo usuário WSL não foi recebida." >&2
    exit 2
  fi
  printf '%s:%s\n' "$NEXO_USER" "$NEXO_PASSWORD" | chpasswd
  unset NEXO_PASSWORD
elif ((CREATED_USER)); then
  echo "Um usuário novo exige senha." >&2
  exit 2
fi

usermod -a -G audio,sudo "$NEXO_USER"
loginctl enable-linger "$NEXO_USER" >/dev/null 2>&1 || true

python3 - "$NEXO_USER" <<'PY'
import configparser
import os
import sys
import tempfile
from pathlib import Path

user = sys.argv[1]
target = Path("/etc/wsl.conf")
config = configparser.ConfigParser()
config.optionxform = str
if target.exists():
    config.read(target, encoding="utf-8")
if not config.has_section("boot"):
    config.add_section("boot")
config.set("boot", "systemd", "true")
if not config.has_section("user"):
    config.add_section("user")
config.set("user", "default", user)

descriptor, temporary = tempfile.mkstemp(prefix="wsl.conf.", dir="/etc")
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        config.write(handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, target)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY

install -d -m 0755 -o "$NEXO_USER" -g "$NEXO_USER" "/home/$NEXO_USER"
echo "NEXO_ROOT_BOOTSTRAP_OK=1"
echo "NEXO_RESTART_REQUIRED=1"
