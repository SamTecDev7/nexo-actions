from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installer/wsl/install-nexo.sh"
FAKE_REPOSITORY = "https://github.com/example/nexo-actions.git"
COMMIT_A = "1" * 40
COMMIT_B = "2" * 40
SECRET_A = "ci-placeholder-openai-value"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class WslInstallerSimulationTests(unittest.TestCase):
    """Exercise the real WSL installer with command-level, offline test doubles.

    The test deliberately does not claim to validate apt, systemd or hardware.
    It validates installer orchestration and filesystem effects without touching
    the developer's checkout, venv, services, credentials or network.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="nexo-clean-install-")
        self.root = Path(self.temporary.name)
        self.home = self.root / "home" / "nexo"
        self.home.mkdir(parents=True)
        self.bin = self.root / "mock-bin"
        self.bin.mkdir()
        self.command_log = self.root / "commands.log"
        self.install_dir = self.home / "nexo-actions"
        self.venv_dir = self.home / ".venvs" / "ovos"
        self.real_python = sys.executable
        self._install_mocks()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _install_mocks(self) -> None:
        _write_executable(
            self.bin / "git",
            r'''#!/usr/bin/env bash
set -euo pipefail
printf 'git' >>"$NEXO_SIM_COMMAND_LOG"
printf ' %q' "$@" >>"$NEXO_SIM_COMMAND_LOG"
printf '\n' >>"$NEXO_SIM_COMMAND_LOG"

repository=""
if [[ "${1:-}" == "-C" ]]; then
  repository="$2"
  shift 2
fi
operation="${1:-}"
shift || true
case "$operation" in
  clone)
    destination="${@: -1}"
    mkdir -p "$destination/.git" "$destination/scripts" "$destination/installer/wsl"
    printf '# simulated requirements\n' >"$destination/installer/wsl/requirements-ovos.txt"
    cat >"$destination/scripts/setup_wsl.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'setup_wsl NEXO_VENV=%q\n' "${NEXO_VENV:-}" >>"$NEXO_SIM_COMMAND_LOG"
unit_root="$HOME/.config/systemd/user"
mkdir -p "$unit_root"
for unit in nexo-pulse-aec nexo-audio-watchdog nexo-ui; do
  printf '[Service]\nExecStart=/bin/true\n' >"$unit_root/$unit.service"
done
EOF
    chmod +x "$destination/scripts/setup_wsl.sh"
    ;;
  fetch|remote)
    ;;
  diff)
    [[ ! -e "$repository/.mock_dirty" ]]
    ;;
  checkout)
    printf '%s\n' "${@: -1}" >"$repository/.mock_head"
    ;;
  rev-parse)
    candidate="${@: -1}"
    if [[ "$candidate" == "HEAD" ]]; then
      cat "$repository/.mock_head"
    else
      printf '%s\n' "$NEXO_SIM_COMMIT"
    fi
    ;;
  *)
    printf 'unsupported mocked git operation: %s\n' "$operation" >&2
    exit 90
    ;;
esac
''',
        )
        _write_executable(
            self.bin / "python3",
            r'''#!/usr/bin/env bash
set -euo pipefail
printf 'python3' >>"$NEXO_SIM_COMMAND_LOG"
printf ' %q' "$@" >>"$NEXO_SIM_COMMAND_LOG"
printf '\n' >>"$NEXO_SIM_COMMAND_LOG"
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
  venv="${3:?missing venv path}"
  mkdir -p "$venv/bin"
  cat >"$venv/bin/python" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'venv-python' >>"$NEXO_SIM_COMMAND_LOG"
printf ' %q' "$@" >>"$NEXO_SIM_COMMAND_LOG"
printf '\n' >>"$NEXO_SIM_COMMAND_LOG"
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
  exit 0
fi
if [[ "${1:-}" == "-" && $# -ge 2 ]]; then
  exec "$NEXO_SIM_REAL_PYTHON" "$@"
fi
if [[ "${1:-}" == "-" ]]; then
  cat >/dev/null
  printf 'NEXO_OPENAI_PROFILE_OK=1\n'
  exit 0
fi
if [[ "${1:-}" == *check_openai_connection.py ]]; then
  exit 0
fi
printf 'unexpected simulated venv Python call: %q\n' "$*" >&2
exit 91
EOF
  chmod +x "$venv/bin/python"
  exit 0
fi
exec "$NEXO_SIM_REAL_PYTHON" "$@"
''',
        )
        _write_executable(
            self.bin / "systemctl",
            r'''#!/usr/bin/env bash
set -euo pipefail
printf 'systemctl' >>"$NEXO_SIM_COMMAND_LOG"
printf ' %q' "$@" >>"$NEXO_SIM_COMMAND_LOG"
printf '\n' >>"$NEXO_SIM_COMMAND_LOG"
exit 0
''',
        )

    def _environment(self, commit: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(self.home),
                "PATH": str(self.bin) + os.pathsep + environment.get("PATH", ""),
                "XDG_STATE_HOME": str(self.home / ".local" / "state"),
                "NEXO_SIM_COMMAND_LOG": str(self.command_log),
                "NEXO_SIM_COMMIT": commit,
                "NEXO_SIM_REAL_PYTHON": self.real_python,
            }
        )
        return environment

    def _run(
        self,
        *,
        commit: str,
        key: str | None = None,
        preserve_key: bool = False,
        repair: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "bash",
            str(INSTALLER),
            "--repository-url",
            FAKE_REPOSITORY,
            "--source-ref",
            commit,
            "--install-dir",
            str(self.install_dir),
            "--venv",
            str(self.venv_dir),
            "--skip-openai-validation",
        ]
        if key is not None:
            command.append("--read-key-stdin")
        if preserve_key:
            command.append("--preserve-key")
        if repair:
            command.append("--skip-git-update")
        return subprocess.run(
            command,
            input=(key + "\n") if key is not None else None,
            capture_output=True,
            text=True,
            timeout=30,
            env=self._environment(commit),
        )

    def test_clean_install_creates_private_secret_config_units_and_detached_commit(self):
        result = self._run(commit=COMMIT_A, key=SECRET_A)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NEXO_WSL_INSTALL_OK=1", result.stdout)
        self.assertEqual((self.install_dir / ".mock_head").read_text().strip(), COMMIT_A)

        env_file = self.install_dir / ".env"
        self.assertEqual(env_file.read_text(encoding="utf-8"), f"OPENAI_API_KEY={SECRET_A}\n")
        self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)
        install_log = self.home / ".local" / "state" / "nexo" / "install.log"
        self.assertNotIn(SECRET_A, install_log.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(install_log.stat().st_mode), 0o600)

        config = self.home / ".config" / "mycroft" / "mycroft.conf"
        self.assertTrue(config.is_file())
        unit_root = self.home / ".config" / "systemd" / "user"
        self.assertIn(str(self.venv_dir), (unit_root / "ovos-messagebus.service").read_text())
        commands = self.command_log.read_text(encoding="utf-8")
        self.assertIn("systemctl --user enable", commands)
        self.assertIn("setup_wsl", commands)

    def test_update_changes_commit_but_preserves_existing_key(self):
        first = self._run(commit=COMMIT_A, key=SECRET_A)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = (self.install_dir / ".env").read_bytes()
        self.command_log.write_text("", encoding="utf-8")

        updated = self._run(commit=COMMIT_B, preserve_key=True)
        self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)
        self.assertEqual((self.install_dir / ".mock_head").read_text().strip(), COMMIT_B)
        self.assertEqual((self.install_dir / ".env").read_bytes(), before)
        commands = self.command_log.read_text(encoding="utf-8")
        self.assertIn("fetch --prune --tags origin", commands)
        self.assertIn(f"checkout --detach {COMMIT_B}", commands)

    def test_repair_keeps_exact_checkout_and_secret_without_fetching(self):
        first = self._run(commit=COMMIT_A, key=SECRET_A)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.command_log.write_text("", encoding="utf-8")

        repaired = self._run(commit=COMMIT_B, preserve_key=True, repair=True)
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
        self.assertIn("Modo reparo", repaired.stdout)
        self.assertEqual((self.install_dir / ".mock_head").read_text().strip(), COMMIT_A)
        commands = self.command_log.read_text(encoding="utf-8")
        self.assertNotIn("fetch", commands)
        self.assertNotIn("checkout", commands)

    def test_update_refuses_dirty_checkout_before_mutating_secret(self):
        first = self._run(commit=COMMIT_A, key=SECRET_A)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = (self.install_dir / ".env").read_bytes()
        (self.install_dir / ".mock_dirty").touch()

        refused = self._run(commit=COMMIT_B, preserve_key=True)
        self.assertEqual(refused.returncode, 3, refused.stdout + refused.stderr)
        self.assertIn("alterações rastreadas", refused.stdout)
        self.assertEqual((self.install_dir / ".mock_head").read_text().strip(), COMMIT_A)
        self.assertEqual((self.install_dir / ".env").read_bytes(), before)

    def test_repair_requires_existing_checkout(self):
        refused = self._run(commit=COMMIT_A, preserve_key=True, repair=True)
        self.assertEqual(refused.returncode, 3, refused.stdout + refused.stderr)
        self.assertIn("instalação existente", refused.stdout)


if __name__ == "__main__":
    unittest.main()
