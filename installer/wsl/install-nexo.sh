#!/usr/bin/env bash
set -euo pipefail

# Idempotent, user-scoped Nexo installation.  The OpenAI key is accepted only
# through stdin and is never placed in argv, stdout or the installer log.

REPOSITORY_URL=""
SOURCE_REF="main"
INSTALL_DIR=""
VENV_DIR=""
READ_KEY_STDIN=0
PRESERVE_KEY=0
VALIDATE_OPENAI=1
SKIP_GIT_UPDATE=0
TRANSACTION_STARTED=0
TRANSACTION_SUCCESS=0
HAD_EXISTING_CHECKOUT=0
CHECKOUT_CREATED=0
PREVIOUS_COMMIT=""
PREVIOUS_ORIGIN=""
VENV_BACKUP=""
VENV_CREATED=0
ENV_TEMP=""
ENV_BACKUP=""
ENV_WAS_PRESENT=0
ENV_CHANGED=0
NEXO_CONFIG_PATH="$HOME/.config/nexo/config.json"
MYCROFT_CONFIG="$HOME/.config/mycroft/mycroft.conf"
NEXO_CONFIG_BACKUP=""
MYCROFT_CONFIG_BACKUP=""
NEXO_CONFIG_EXISTED=0
MYCROFT_CONFIG_EXISTED=0
ACTIVE_UNITS=()
SERVICE_UNITS=(
  nexo-ui.service
  ovos-listener.service
  ovos-core.service
  ovos-audio.service
  ovos-phal.service
  nexo-audio-watchdog.service
  nexo-pulse-aec.service
  ovos-messagebus.service
  nexo-neural-tts.service
)

validate_supported_runtime() {
  if [[ ! -r /etc/os-release ]]; then
    echo "Não foi possível identificar a distribuição Linux." >&2
    return 2
  fi
  local distro_id distro_version
  distro_id="$(. /etc/os-release; printf '%s' "${ID:-}")"
  distro_version="$(. /etc/os-release; printf '%s' "${VERSION_ID:-}")"
  if [[ "$distro_id" != "ubuntu" || ! "$distro_version" =~ ^(22\.04|24\.04)$ ]]; then
    echo "O Nexo suporta Ubuntu 22.04 ou 24.04; detectado: $distro_id $distro_version." >&2
    return 2
  fi
  python3 - <<'PY'
import sys
if not ((3, 10) <= sys.version_info[:2] < (3, 13)):
    raise SystemExit(
        f"O Nexo requer Python 3.10, 3.11 ou 3.12; detectado {sys.version.split()[0]}"
    )
PY
}

canonical_user_path() {
  local requested="$1"
  local label="$2"
  local home_real relative cursor component resolved
  home_real="$(realpath -e -- "$HOME")"
  case "$requested" in
    "$HOME"/*) relative="${requested#"$HOME"/}" ;;
    *) echo "$label deve ficar estritamente dentro de $HOME." >&2; return 2 ;;
  esac
  if [[ -z "$relative" || "$relative" == */ || "$relative" == /* ]]; then
    echo "$label não pode ser a HOME, terminar em barra ou conter barras duplicadas." >&2
    return 2
  fi
  cursor="$home_real"
  IFS='/' read -r -a _nexo_parts <<<"$relative"
  for component in "${_nexo_parts[@]}"; do
    if [[ -z "$component" || "$component" == "." || "$component" == ".." ||
          ! "$component" =~ ^[A-Za-z0-9._-]+$ ]]; then
      echo "$label contém um componente inválido." >&2
      return 2
    fi
    cursor="$cursor/$component"
    if [[ -L "$cursor" ]]; then
      echo "$label não pode atravessar link simbólico: $cursor" >&2
      return 2
    fi
  done
  resolved="$(realpath -m -- "$requested")"
  case "$resolved" in
    "$home_real"/*) printf '%s\n' "$resolved" ;;
    *) echo "$label escapou da HOME após canonicalização." >&2; return 2 ;;
  esac
}

resolve_source_commit() {
  local repository="$1"
  local source_ref="${2:-$SOURCE_REF}"
  local candidate resolved branch_ref
  git -C "$repository" fetch --prune --tags origin
  if [[ "$source_ref" =~ ^[0-9a-fA-F]{40}$ ]]; then
    resolved="$(git -C "$repository" rev-parse --verify "$source_ref^{commit}" 2>/dev/null || true)"
    if [[ -z "$resolved" ]]; then
      git -C "$repository" fetch origin "$source_ref"
      resolved="$(git -C "$repository" rev-parse --verify 'FETCH_HEAD^{commit}')"
    fi
    printf '%s\n' "$resolved"
    return 0
  fi
  if [[ "$source_ref" == refs/tags/* ]]; then
    resolved="$(git -C "$repository" rev-parse --verify "$source_ref^{commit}")"
    printf '%s\n' "$resolved"
    return 0
  fi
  branch_ref="$source_ref"
  branch_ref="${branch_ref#refs/heads/}"
  branch_ref="${branch_ref#refs/remotes/origin/}"
  branch_ref="${branch_ref#origin/}"
  candidate="refs/remotes/origin/$branch_ref^{commit}"
  if resolved="$(git -C "$repository" rev-parse --verify "$candidate" 2>/dev/null)"; then
    printf '%s\n' "$resolved"
    return 0
  fi
  candidate="refs/tags/$source_ref"
  if resolved="$(git -C "$repository" rev-parse --verify "$candidate^{commit}" 2>/dev/null)"; then
    printf '%s\n' "$resolved"
    return 0
  fi
  git -C "$repository" fetch origin "$source_ref"
  git -C "$repository" rev-parse --verify 'FETCH_HEAD^{commit}'
}

merge_openai_key() {
  local env_path="$1"
  local key="$2"
  local line replaced=0
  if [[ -L "$env_path" ]]; then
    echo "Recusando atualizar .env por link simbólico: $env_path" >&2
    return 2
  fi
  ENV_WAS_PRESENT=0
  if [[ -f "$env_path" ]]; then
    ENV_WAS_PRESENT=1
    mkdir -p "$STATE_ROOT/backups"
    chmod 700 "$STATE_ROOT/backups"
    ENV_BACKUP="$STATE_ROOT/backups/env-$(date +%Y%m%d-%H%M%S)-$$.backup"
    cp -p -- "$env_path" "$ENV_BACKUP"
    chmod 600 "$ENV_BACKUP"
  fi
  ENV_TEMP="$(mktemp "$env_path.XXXXXX")"
  chmod 600 "$ENV_TEMP"
  if [[ -f "$env_path" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?OPENAI_API_KEY[[:space:]]*= ]]; then
        if ((replaced == 0)); then
          printf 'OPENAI_API_KEY=%s\n' "$key" >>"$ENV_TEMP"
          replaced=1
        fi
      else
        printf '%s\n' "$line" >>"$ENV_TEMP"
      fi
    done <"$env_path"
  fi
  if ((replaced == 0)); then
    printf 'OPENAI_API_KEY=%s\n' "$key" >>"$ENV_TEMP"
  fi
  mv -f -- "$ENV_TEMP" "$env_path"
  ENV_TEMP=""
  chmod 600 "$env_path"
  ENV_CHANGED=1
}

rollback_env() {
  local env_path="$1"
  local restore_temp=""
  if ((ENV_CHANGED == 0)); then return 0; fi
  if ((ENV_WAS_PRESENT)) && [[ -f "$ENV_BACKUP" ]]; then
    restore_temp="$(mktemp "$env_path.rollback.XXXXXX")"
    cp -- "$ENV_BACKUP" "$restore_temp"
    chmod 600 "$restore_temp"
    mv -f -- "$restore_temp" "$env_path"
  else
    rm -f -- "$env_path"
  fi
}

remove_tree_safe() {
  local target="$1"
  local resolved home_real
  [[ -n "$target" && "$target" != "/" && "$target" != "$HOME" ]] || return 2
  home_real="$(realpath -e -- "$HOME")"
  resolved="$(realpath -m -- "$target")"
  case "$resolved" in
    "$home_real"/*) rm -rf -- "$resolved" ;;
    *) echo "Recusando remover árvore fora da HOME: $resolved" >&2; return 2 ;;
  esac
}

backup_optional_config() {
  local source="$1" destination="$2"
  if [[ -L "$source" ]]; then
    echo "Recusando alterar configuração por link simbólico: $source" >&2
    return 2
  fi
  if [[ -f "$source" ]]; then
    cp -p -- "$source" "$destination"
    chmod 600 "$destination"
    return 0
  fi
  return 1
}

stop_transaction_services() {
  local unit
  ACTIVE_UNITS=()
  for unit in "${SERVICE_UNITS[@]}"; do
    if systemctl --user is-active --quiet "$unit" 2>/dev/null; then
      ACTIVE_UNITS+=("$unit")
    fi
  done
  if (("${#ACTIVE_UNITS[@]}" > 0)); then
    systemctl --user stop "${ACTIVE_UNITS[@]}"
  fi
}

restore_transaction() {
  set +e
  echo "Falha detectada; restaurando checkout, ambiente e configurações anteriores." >&2
  systemctl --user stop "${SERVICE_UNITS[@]}" >/dev/null 2>&1 || true
  rollback_env "$INSTALL_DIR/.env"
  if ((NEXO_CONFIG_EXISTED)) && [[ -f "$NEXO_CONFIG_BACKUP" ]]; then
    mkdir -p "$(dirname "$NEXO_CONFIG_PATH")"
    cp -p -- "$NEXO_CONFIG_BACKUP" "$NEXO_CONFIG_PATH"
  elif ((TRANSACTION_STARTED)); then
    rm -f -- "$NEXO_CONFIG_PATH"
  fi
  if ((MYCROFT_CONFIG_EXISTED)) && [[ -f "$MYCROFT_CONFIG_BACKUP" ]]; then
    mkdir -p "$(dirname "$MYCROFT_CONFIG")"
    cp -p -- "$MYCROFT_CONFIG_BACKUP" "$MYCROFT_CONFIG"
  elif ((TRANSACTION_STARTED)); then
    rm -f -- "$MYCROFT_CONFIG"
  fi
  if ((VENV_CREATED)) && [[ -e "$VENV_DIR" ]]; then
    remove_tree_safe "$VENV_DIR" || true
  fi
  if [[ -n "$VENV_BACKUP" && -d "$VENV_BACKUP" ]]; then
    mv -- "$VENV_BACKUP" "$VENV_DIR"
  fi
  if ((HAD_EXISTING_CHECKOUT)) && [[ -n "$PREVIOUS_COMMIT" ]]; then
    git -C "$INSTALL_DIR" checkout --detach "$PREVIOUS_COMMIT" >/dev/null 2>&1 || true
    if [[ -n "$PREVIOUS_ORIGIN" ]]; then
      git -C "$INSTALL_DIR" remote set-url origin "$PREVIOUS_ORIGIN" >/dev/null 2>&1 || true
    fi
  elif ((CHECKOUT_CREATED)) && [[ -e "$INSTALL_DIR" ]]; then
    remove_tree_safe "$INSTALL_DIR" || true
  fi
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  if (("${#ACTIVE_UNITS[@]}" > 0)); then
    systemctl --user start "${ACTIVE_UNITS[@]}" >/dev/null 2>&1 || true
  fi
}

on_exit() {
  local status=$?
  trap - EXIT
  set +e
  [[ -z "$ENV_TEMP" ]] || rm -f -- "$ENV_TEMP"
  if ((status != 0 && TRANSACTION_STARTED && ! TRANSACTION_SUCCESS)); then
    restore_transaction
  fi
  if ((status == 0 && TRANSACTION_SUCCESS)); then
    if [[ -n "$VENV_BACKUP" && -d "$VENV_BACKUP" ]]; then
      remove_tree_safe "$VENV_BACKUP" || true
    fi
    if [[ -n "${TRANSACTION_DIR:-}" && -d "$TRANSACTION_DIR" ]]; then
      remove_tree_safe "$TRANSACTION_DIR" || true
    fi
  fi
  exit "$status"
}
trap on_exit EXIT

if [[ "${NEXO_INSTALLER_TEST_MODE:-0}" == "1" ]]; then
  case "${1:-}" in
    --validate-install-path)
      canonical_user_path "${2:-}" "A pasta do Nexo"
      exit
      ;;
    --resolve-source)
      resolve_source_commit "${2:-}" "${3:-main}"
      exit
      ;;
    --merge-env|--merge-env-then-rollback)
      STATE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/nexo-test"
      mkdir -p "$STATE_ROOT"
      chmod 700 "$STATE_ROOT"
      IFS= read -r OPENAI_KEY || true
      merge_openai_key "${2:-}" "$OPENAI_KEY"
      unset OPENAI_KEY
      if [[ "$1" == "--merge-env-then-rollback" ]]; then
        rollback_env "${2:-}"
      fi
      exit
      ;;
    --simulate-venv-failure)
      VENV_DIR="$(canonical_user_path "${2:-}" "O ambiente Python")"
      VENV_BACKUP="$VENV_DIR.nexo-test-backup-$$"
      mv -- "$VENV_DIR" "$VENV_BACKUP"
      VENV_CREATED=1
      mkdir -p "$VENV_DIR"
      printf 'parcial\n' >"$VENV_DIR/partial.marker"
      restore_transaction
      [[ -f "$VENV_DIR/original.marker" && ! -e "$VENV_DIR/partial.marker" ]]
      exit
      ;;
    --hold-lock)
      STATE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/nexo-test"
      mkdir -p "$STATE_ROOT"
      exec 8>"$STATE_ROOT/install.lock"
      flock -n 8 || exit 4
      printf 'LOCKED\n'
      sleep "${2:-1}"
      exit
      ;;
    --check-lock)
      STATE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/nexo-test"
      mkdir -p "$STATE_ROOT"
      exec 8>"$STATE_ROOT/install.lock"
      flock -n 8 || exit 4
      printf 'AVAILABLE\n'
      exit
      ;;
    *) echo "Operação de teste inválida." >&2; exit 2 ;;
  esac
fi

while (($#)); do
  case "$1" in
    --repository-url) REPOSITORY_URL="${2:-}"; shift 2 ;;
    --source-ref) SOURCE_REF="${2:-}"; shift 2 ;;
    --branch) SOURCE_REF="${2:-}"; shift 2 ;;
    --install-dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    --venv) VENV_DIR="${2:-}"; shift 2 ;;
    --read-key-stdin) READ_KEY_STDIN=1; shift ;;
    --preserve-key) PRESERVE_KEY=1; shift ;;
    --skip-openai-validation) VALIDATE_OPENAI=0; shift ;;
    --skip-git-update) SKIP_GIT_UPDATE=1; shift ;;
    *) echo "Argumento desconhecido: $1" >&2; exit 2 ;;
  esac
done

if [[ ! "$REPOSITORY_URL" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]]; then
  echo "Use uma URL HTTPS de repositório do GitHub." >&2
  exit 2
fi
if [[ ! "$SOURCE_REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ || "$SOURCE_REF" == *..* ]]; then
  echo "Referência Git inválida." >&2
  exit 2
fi

INSTALL_DIR="${INSTALL_DIR:-$HOME/nexo-actions}"
VENV_DIR="${VENV_DIR:-$HOME/.venvs/ovos}"
INSTALL_DIR="$(canonical_user_path "$INSTALL_DIR" "A pasta do Nexo")"
VENV_DIR="$(canonical_user_path "$VENV_DIR" "O ambiente Python")"
validate_supported_runtime

STATE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/nexo"
mkdir -p "$STATE_ROOT"
chmod 700 "$STATE_ROOT"
if ! command -v flock >/dev/null 2>&1; then
  echo "flock é obrigatório para impedir instalações concorrentes." >&2
  exit 2
fi
exec 8>"$STATE_ROOT/install.lock"
if ! flock -n 8; then
  echo "Outra instalação ou reparo do Nexo já está em andamento." >&2
  exit 4
fi
INSTALL_LOG="$STATE_ROOT/install.log"
touch "$INSTALL_LOG"
chmod 600 "$INSTALL_LOG"
exec > >(tee -a "$INSTALL_LOG") 2>&1

echo "[$(date -Is)] Iniciando instalação idempotente do Nexo."

OPENAI_KEY=""
if ((READ_KEY_STDIN)); then
  IFS= read -r OPENAI_KEY || true
fi
if [[ -z "$OPENAI_KEY" && $PRESERVE_KEY -eq 0 ]]; then
  echo "A chave OpenAI não foi recebida pelo canal seguro." >&2
  exit 2
fi

mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  HAD_EXISTING_CHECKOUT=1
  PREVIOUS_COMMIT="$(git -C "$INSTALL_DIR" rev-parse --verify HEAD)"
  PREVIOUS_ORIGIN="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
  if ! git -C "$INSTALL_DIR" diff --quiet || ! git -C "$INSTALL_DIR" diff --cached --quiet; then
    echo "Há alterações rastreadas no checkout; atualização recusada para evitar uma instalação híbrida." >&2
    echo "Faça commit ou gere um patch e execute novamente. Arquivos ignorados, como .env, são preservados." >&2
    exit 3
  fi
fi

TRANSACTION_DIR="$STATE_ROOT/transactions/$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$TRANSACTION_DIR"
chmod 700 "$TRANSACTION_DIR"
if backup_optional_config "$NEXO_CONFIG_PATH" "$TRANSACTION_DIR/nexo-config.json"; then
  NEXO_CONFIG_EXISTED=1
  NEXO_CONFIG_BACKUP="$TRANSACTION_DIR/nexo-config.json"
fi
if backup_optional_config "$MYCROFT_CONFIG" "$TRANSACTION_DIR/mycroft.conf"; then
  MYCROFT_CONFIG_EXISTED=1
  MYCROFT_CONFIG_BACKUP="$TRANSACTION_DIR/mycroft.conf"
fi
TRANSACTION_STARTED=1
stop_transaction_services

if ((HAD_EXISTING_CHECKOUT)); then
  if ((SKIP_GIT_UPDATE)); then
    echo "Modo reparo: preservando exatamente o checkout Git atual."
  else
    echo "Atualizando o repositório existente sem descartar alterações locais..."
    git -C "$INSTALL_DIR" remote set-url origin "$REPOSITORY_URL"
    NEXO_TARGET_COMMIT="$(resolve_source_commit "$INSTALL_DIR" "$SOURCE_REF")"
    git -C "$INSTALL_DIR" checkout --detach "$NEXO_TARGET_COMMIT"
  fi
elif [[ -e "$INSTALL_DIR" ]]; then
  if ((SKIP_GIT_UPDATE)); then
    echo "Modo reparo exige um checkout Git existente em $INSTALL_DIR." >&2
    exit 3
  fi
  if [[ -n "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    echo "A pasta existe e não é um checkout Git: $INSTALL_DIR" >&2
    exit 3
  fi
  rmdir "$INSTALL_DIR"
  CHECKOUT_CREATED=1
  git clone --no-checkout "$REPOSITORY_URL" "$INSTALL_DIR"
  NEXO_TARGET_COMMIT="$(resolve_source_commit "$INSTALL_DIR" "$SOURCE_REF")"
  git -C "$INSTALL_DIR" checkout --detach "$NEXO_TARGET_COMMIT"
else
  if ((SKIP_GIT_UPDATE)); then
    echo "Modo reparo exige uma instalação existente em $INSTALL_DIR." >&2
    exit 3
  fi
  CHECKOUT_CREATED=1
  git clone --no-checkout "$REPOSITORY_URL" "$INSTALL_DIR"
  NEXO_TARGET_COMMIT="$(resolve_source_commit "$INSTALL_DIR" "$SOURCE_REF")"
  git -C "$INSTALL_DIR" checkout --detach "$NEXO_TARGET_COMMIT"
fi

if [[ -e "$VENV_DIR" ]]; then
  VENV_BACKUP="$VENV_DIR.nexo-rollback-$(date +%Y%m%d-%H%M%S)-$$"
  mv -- "$VENV_DIR" "$VENV_BACKUP"
fi
VENV_CREATED=1
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check \
  pip==26.2.1 setuptools==81.0.0 wheel==0.47.0
"$VENV_DIR/bin/python" -m pip install \
  --disable-pip-version-check \
  -r "$INSTALL_DIR/installer/wsl/requirements-ovos.txt"
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -e "$INSTALL_DIR"

if [[ -L "$INSTALL_DIR/.env" ]]; then
  echo "Recusando usar .env por link simbólico: $INSTALL_DIR/.env" >&2
  exit 2
fi
if [[ -n "$OPENAI_KEY" ]]; then
  merge_openai_key "$INSTALL_DIR/.env" "$OPENAI_KEY"
  unset OPENAI_KEY
elif [[ ! -s "$INSTALL_DIR/.env" ]]; then
  echo "Não há chave anterior para preservar em $INSTALL_DIR/.env." >&2
  exit 2
fi
chmod 600 "$INSTALL_DIR/.env"
mkdir -p "$(dirname "$MYCROFT_CONFIG")"
"$VENV_DIR/bin/python" - "$MYCROFT_CONFIG" <<'PY'
import json
import os
import tempfile
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except (OSError, ValueError):
    raise SystemExit("mycroft.conf existe, mas não contém JSON válido")
if not isinstance(config, dict):
    raise SystemExit("mycroft.conf deve conter um objeto JSON")

config.setdefault("lang", "pt-BR")
config.setdefault("log_level", "INFO")
config.setdefault("websocket", {}).update({
    "host": "127.0.0.1", "port": 8181, "route": "/core", "ssl": False,
})
listener = config.setdefault("listener", {})
listener.setdefault("wake_word", "nexo")
listener.setdefault("VAD", {"module": "ovos-vad-plugin-silero"})
listener.setdefault("microphone", {
    "module": "ovos-microphone-plugin-alsa",
    "ovos-microphone-plugin-alsa": {"device": "pulse", "multiplier": 1.0},
})
listener.update({
    "instant_listen": False,
    "mute_during_output": False,
    "fake_barge_in": False,
})
config.setdefault("hotwords", {}).setdefault("nexo", {}).update({
    "module": "ovos-ww-plugin-vosk",
    "listen": True,
    "lang": "pt-BR",
    "samples": ["nexo", "anexo"],
    "rule": "contains",
    "full_vocab": False,
})
# The Realtime session owns speech after the wake word.  Tiny/int8 keeps the
# OVOS fallback inexpensive and avoids installing a large local model.
stt = config.setdefault("stt", {})
stt.setdefault("module", "nexo-smart-stt")
nexo_stt = stt.setdefault("nexo-smart-stt", {})
for key, value in {
    "model": "tiny",
    "use_cuda": False,
    "compute_type": "int8",
    "beam_size": 1,
    "vad_filter": True,
}.items():
    nexo_stt.setdefault(key, value)
config.setdefault("tts", {}).setdefault("module", "nexo-hybrid-tts")
config["confirm_listening"] = True

path.parent.mkdir(parents=True, exist_ok=True)
descriptor, temporary = tempfile.mkstemp(prefix="mycroft.conf.", dir=path.parent)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY

UNIT_ROOT="$HOME/.config/systemd/user"
mkdir -p "$UNIT_ROOT"

cat >"$UNIT_ROOT/ovos-messagebus.service" <<EOF
[Unit]
Description=Open Voice OS - Message bus
After=network-online.target

[Service]
ExecStart=$VENV_DIR/bin/ovos-messagebus
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

cat >"$UNIT_ROOT/ovos-phal.service" <<EOF
[Unit]
Description=Open Voice OS - PHAL
After=ovos-messagebus.service
Requires=ovos-messagebus.service

[Service]
WorkingDirectory=$VENV_DIR
ExecStart=$VENV_DIR/bin/ovos_PHAL
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

cat >"$UNIT_ROOT/ovos-audio.service" <<EOF
[Unit]
Description=Open Voice OS - Audio
After=ovos-messagebus.service nexo-pulse-aec.service
Requires=ovos-messagebus.service

[Service]
WorkingDirectory=$VENV_DIR
ExecStart=$VENV_DIR/bin/ovos-audio
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

cat >"$UNIT_ROOT/ovos-core.service" <<EOF
[Unit]
Description=Open Voice OS - Core and Nexo skill
After=ovos-messagebus.service ovos-phal.service ovos-audio.service
Requires=ovos-messagebus.service ovos-audio.service

[Service]
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/ovos-core
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

cat >"$UNIT_ROOT/ovos-listener.service" <<EOF
[Unit]
Description=Open Voice OS - Listener
After=ovos-core.service nexo-pulse-aec.service
Requires=ovos-core.service

[Service]
WorkingDirectory=$VENV_DIR
ExecStart=$VENV_DIR/bin/ovos-dinkum-listener
Restart=on-failure
RestartSec=3
TimeoutStartSec=5min

[Install]
WantedBy=default.target
EOF

chmod 644 "$UNIT_ROOT"/ovos-*.service
systemctl --user daemon-reload
systemctl --user enable ovos-messagebus.service ovos-phal.service \
  ovos-audio.service ovos-core.service ovos-listener.service

echo "Aplicando configuração OpenAI-first, áudio e interface..."
NEXO_VENV="$VENV_DIR" bash "$INSTALL_DIR/scripts/setup_wsl.sh"

systemctl --user daemon-reload
systemctl --user enable \
  ovos-messagebus.service nexo-pulse-aec.service ovos-phal.service \
  ovos-audio.service ovos-core.service ovos-listener.service \
  nexo-audio-watchdog.service nexo-ui.service

# Reinicie em ordem para que cada consumidor só veja dependências já novas e
# saudáveis. Isso evita misturar processos do venv antigo com o recém-criado.
for unit in \
  nexo-pulse-aec.service \
  ovos-messagebus.service \
  ovos-phal.service \
  ovos-audio.service \
  ovos-core.service \
  ovos-listener.service \
  nexo-audio-watchdog.service \
  nexo-ui.service; do
  systemctl --user restart "$unit"
done
for unit in "${ACTIVE_UNITS[@]}"; do
  if [[ "$unit" == "nexo-neural-tts.service" ]]; then
    systemctl --user restart "$unit"
  fi
done
for unit in \
  nexo-pulse-aec.service \
  ovos-messagebus.service \
  ovos-phal.service \
  ovos-audio.service \
  ovos-core.service \
  ovos-listener.service \
  nexo-audio-watchdog.service \
  nexo-ui.service; do
  systemctl --user is-active --quiet "$unit"
done

if ((VALIDATE_OPENAI)); then
  "$VENV_DIR/bin/python" "$INSTALL_DIR/scripts/check_openai_connection.py" \
    --env-file "$INSTALL_DIR/.env"
fi

"$VENV_DIR/bin/python" - <<'PY'
from nexo_actions.config import load_config
from nexo_actions.secrets import openai_credential_status

config = load_config(force=True)
assert config["ai"]["provider"] == "openai"
assert config["ai"]["fallback_enabled"] is False
credential = openai_credential_status()
assert credential["configured"] and credential["secure_storage"]
print("NEXO_OPENAI_PROFILE_OK=1")
PY

NEXO_INSTALLED_COMMIT="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
TRANSACTION_SUCCESS=1
echo "NEXO_WSL_INSTALL_OK=1"
echo "NEXO_INSTALL_DIR=$INSTALL_DIR"
echo "NEXO_VENV_DIR=$VENV_DIR"
echo "NEXO_INSTALL_LOG=$INSTALL_LOG"
echo "NEXO_SOURCE_REF=$NEXO_INSTALLED_COMMIT"
echo "NEXO_SOURCE_COMMIT=$NEXO_INSTALLED_COMMIT"
