from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WSL_INSTALLER = ROOT / "installer/wsl/install-nexo.sh"


def run_wsl_installer_test(
    home: Path,
    *arguments: str,
    standard_input: str = "",
    timeout: int = 20,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "XDG_STATE_HOME": str(home / ".local/state"),
            "NEXO_INSTALLER_TEST_MODE": "1",
        }
    )
    return subprocess.run(
        ["bash", str(WSL_INSTALLER), *arguments],
        input=standard_input,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
    )


class WindowsInstallerContractTests(unittest.TestCase):
    def test_upgrade_preserves_admin_models_voice_stt_and_limits(self):
        module_path = ROOT / "scripts/apply_openai_profile.py"
        spec = importlib.util.spec_from_file_location("nexo_apply_profile_test", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        settings = {
            "ai": {
                "provider": "openai",
                "fallback_provider": "ollama",
                "fallback_enabled": False,
                "profiles": {
                    "openai": {
                        "agent_model": "custom-agent",
                        "realtime_model": "custom-realtime",
                        "voice": "cedar",
                        "reasoning_effort": "high",
                    }
                },
            },
            "stt": {"model": "large-v3", "precision": "float16"},
            "agent": {"max_steps": 19, "timeout_seconds": 240},
        }
        original = json.loads(json.dumps(settings))
        preserved = module._prepare_settings(
            settings,
            already_migrated=True,
            initialize_only=True,
            recommended=True,
        )
        self.assertTrue(preserved)
        self.assertEqual(settings, original)

    def test_installer_sources_and_build_contract_exist(self):
        expected = (
            "installer/windows/Install-Nexo.ps1",
            "installer/windows/NexoSetup.cs",
            "installer/windows/NexoSetup.manifest",
            "installer/wsl/bootstrap-root.sh",
            "installer/wsl/install-nexo.sh",
            "installer/wsl/requirements-ovos.txt",
            "scripts/build_windows_installer.ps1",
            "scripts/installer_security.ps1",
        )
        for relative in expected:
            self.assertTrue((ROOT / relative).is_file(), relative)

        build = (ROOT / "scripts/build_windows_installer.ps1").read_text(encoding="utf-8")
        self.assertIn("NexoSetup.exe", build)
        self.assertIn("GITHUB_SHA", build)
        self.assertIn("__NEXO_GIT_REF__", build)
        self.assertIn("source_ref_embedded", build)

    def test_key_is_secure_input_and_never_a_command_line_argument(self):
        windows = (ROOT / "installer/windows/Install-Nexo.ps1").read_text(encoding="utf-8")
        linux = (ROOT / "installer/wsl/install-nexo.sh").read_text(encoding="utf-8")
        self.assertIn("Read-Host '  OPENAI_API_KEY' -AsSecureString", windows)
        self.assertIn("RedirectStandardInput", windows)
        self.assertIn("ZeroFreeBSTR", windows)
        self.assertIn("--read-key-stdin", linux)
        self.assertIn("IFS= read -r OPENAI_KEY", linux)
        self.assertNotRegex(windows, r"--(?:api[-_]?key|openai[-_]?key)")
        self.assertNotIn("set -x", linux)
        self.assertNotIn("Start-Transcript", windows)

    def test_update_repair_and_uninstall_are_bounded(self):
        windows = (ROOT / "installer/windows/Install-Nexo.ps1").read_text(encoding="utf-8")
        linux = (ROOT / "installer/wsl/install-nexo.sh").read_text(encoding="utf-8")
        self.assertIn("'Install', 'Repair', 'Uninstall'", windows)
        self.assertIn("Digite DESINSTALAR", windows)
        self.assertIn("preservar a chave existente", windows)
        self.assertIn("--preserve-key", linux)
        self.assertIn("--skip-git-update", linux)
        self.assertIn('checkout --detach "$NEXO_TARGET_COMMIT"', linux)
        self.assertIn("fetch --prune --tags origin", linux)
        self.assertIn("rev-parse --verify", linux)
        self.assertIn('diff --cached --quiet', linux)
        self.assertIn('alterações rastreadas', linux)
        self.assertNotIn("git reset --hard", linux)
        self.assertIn("remove_tree_safe", linux)
        self.assertIn('case "$resolved" in', linux)
        self.assertIn("TRANSACTION_SUCCESS=1", linux)
        self.assertIn("restore_transaction", linux)
        self.assertIn("flock -n 8", linux)
        self.assertIn("Local\\Nexo.WindowsSetup", windows)
        setup_source = (ROOT / "installer/windows/NexoSetup.cs").read_text(encoding="utf-8")
        self.assertIn('new Mutex(false, @"Local\\Nexo.WindowsSetup")', setup_source)

    def test_wsl_path_preflight_rejects_parent_and_symlink_escape(self):
        if not shutil.which("bash") or not shutil.which("realpath"):
            self.skipTest("bash/realpath are unavailable")
        with tempfile.TemporaryDirectory(prefix="nexo-path-test-") as temporary:
            root = Path(temporary)
            home = root / "home"
            outside = root / "outside"
            home.mkdir()
            outside.mkdir()

            accepted = run_wsl_installer_test(
                home, "--validate-install-path", str(home / "nexo-actions")
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(accepted.stdout.strip(), str(home / "nexo-actions"))

            for invalid in (
                f"{home}/project/..",
                f"{home}//project",
                f"{home}/project/",
            ):
                rejected = run_wsl_installer_test(
                    home, "--validate-install-path", invalid
                )
                self.assertNotEqual(rejected.returncode, 0, invalid)

            (home / "linked").symlink_to(outside, target_is_directory=True)
            linked = run_wsl_installer_test(
                home,
                "--validate-install-path",
                str(home / "linked/nexo-actions"),
            )
            self.assertNotEqual(linked.returncode, 0)
            self.assertIn("link simbólico", linked.stderr)

    def test_env_merge_preserves_values_and_atomic_rollback(self):
        if not shutil.which("bash"):
            self.skipTest("bash is unavailable")
        with tempfile.TemporaryDirectory(prefix="nexo-env-test-") as temporary:
            home = Path(temporary) / "home"
            project = home / "project"
            project.mkdir(parents=True)
            env_path = project / ".env"
            original = (
                "# configuração do usuário\n"
                "OTHER_SECRET=preservar\n"
                "export OPENAI_API_KEY=old-one\n"
                "OPENAI_API_KEY=old-duplicate\n"
                "FEATURE_FLAG=1\n"
            )
            env_path.write_text(original, encoding="utf-8")
            os.chmod(env_path, 0o644)

            merged = run_wsl_installer_test(
                home,
                "--merge-env",
                str(env_path),
                standard_input="new-test-key\n",
            )
            self.assertEqual(merged.returncode, 0, merged.stderr)
            content = env_path.read_text(encoding="utf-8")
            self.assertIn("OTHER_SECRET=preservar", content)
            self.assertIn("FEATURE_FLAG=1", content)
            self.assertEqual(content.count("OPENAI_API_KEY="), 1)
            self.assertIn("OPENAI_API_KEY=new-test-key", content)
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
            backups = list((home / ".local/state/nexo-test/backups").glob("env-*.backup"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), original)

            env_path.write_text(original, encoding="utf-8")
            rolled_back = run_wsl_installer_test(
                home,
                "--merge-env-then-rollback",
                str(env_path),
                standard_input="another-test-key\n",
            )
            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)

    def test_install_lock_rejects_a_concurrent_process(self):
        if not shutil.which("bash") or not shutil.which("flock"):
            self.skipTest("bash/flock are unavailable")
        with tempfile.TemporaryDirectory(prefix="nexo-lock-test-") as temporary:
            home = Path(temporary) / "home"
            home.mkdir()
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    "XDG_STATE_HOME": str(home / ".local/state"),
                    "NEXO_INSTALLER_TEST_MODE": "1",
                }
            )
            holder = subprocess.Popen(
                ["bash", str(WSL_INSTALLER), "--hold-lock", "5"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            try:
                self.assertEqual(holder.stdout.readline().strip(), "LOCKED")
                contender = run_wsl_installer_test(home, "--check-lock")
                self.assertEqual(contender.returncode, 4, contender.stdout + contender.stderr)
            finally:
                holder.terminate()
                holder.wait(timeout=5)
                if holder.stdout:
                    holder.stdout.close()
                if holder.stderr:
                    holder.stderr.close()

    def test_remote_branch_wins_over_stale_local_branch_and_same_name_tag(self):
        if not shutil.which("bash") or not shutil.which("git"):
            self.skipTest("bash/git are unavailable")
        with tempfile.TemporaryDirectory(prefix="nexo-ref-test-") as temporary:
            root = Path(temporary)
            home = root / "home"
            remote = root / "remote.git"
            author = root / "author"
            installed = root / "installed"
            home.mkdir()

            def git(*arguments: str, cwd: Path | None = None) -> str:
                result = subprocess.run(
                    ["git", *arguments],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            git("init", "--bare", "--initial-branch=main", str(remote))
            git("clone", str(remote), str(author))
            git("config", "user.name", "Nexo Test", cwd=author)
            git("config", "user.email", "nexo-test@example.invalid", cwd=author)
            (author / "version.txt").write_text("one\n", encoding="utf-8")
            git("add", "version.txt", cwd=author)
            git("commit", "-m", "first", cwd=author)
            git("push", "origin", "HEAD:refs/heads/main", cwd=author)
            first = git("rev-parse", "HEAD", cwd=author)
            git("tag", "main", first, cwd=author)
            git("push", "origin", "refs/tags/main", cwd=author)
            git("clone", str(remote), str(installed))

            (author / "version.txt").write_text("two\n", encoding="utf-8")
            git("commit", "-am", "second", cwd=author)
            git("push", "origin", "HEAD:refs/heads/main", cwd=author)
            second = git("rev-parse", "HEAD", cwd=author)

            branch = run_wsl_installer_test(
                home, "--resolve-source", str(installed), "main"
            )
            self.assertEqual(branch.returncode, 0, branch.stderr)
            self.assertEqual(branch.stdout.strip(), second)
            explicit_tag = run_wsl_installer_test(
                home, "--resolve-source", str(installed), "refs/tags/main"
            )
            self.assertEqual(explicit_tag.returncode, 0, explicit_tag.stderr)
            self.assertEqual(explicit_tag.stdout.strip(), first)

    def test_partial_venv_is_removed_before_previous_venv_is_restored(self):
        if not shutil.which("bash"):
            self.skipTest("bash is unavailable")
        with tempfile.TemporaryDirectory(prefix="nexo-venv-test-") as temporary:
            home = Path(temporary) / "home"
            venv = home / ".venvs/ovos"
            venv.mkdir(parents=True)
            (venv / "original.marker").write_text("original\n", encoding="utf-8")
            result = run_wsl_installer_test(
                home, "--simulate-venv-failure", str(venv)
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((venv / "original.marker").is_file())
            self.assertFalse((venv / "partial.marker").exists())

    def test_openai_only_runtime_is_compatibility_pinned(self):
        requirements = (ROOT / "installer/wsl/requirements-ovos.txt").read_text(encoding="utf-8")
        for pin in (
            "ovos-core==2.1.1",
            "ovos-dinkum-listener==0.5.0",
            "ovos-audio==1.2.0",
            "ovos-plugin-manager==2.2.0",
            "ovos-utils==0.8.4",
            "ovos-config==2.1.1",
            "ovos-bus-client==1.5.0",
            "faster-whisper==1.2.1",
            "numpy==1.26.4",
            "websocket-client==1.9.0",
            "setuptools==81.0.0",
        ):
            self.assertIn(pin, requirements)
        self.assertNotIn("ollama", requirements.casefold())

    def test_launcher_uses_generated_runtime_config(self):
        launcher = (ROOT / "windows/NexoLauncher.cs").read_text(encoding="utf-8")
        setup = (ROOT / "scripts/setup_windows.ps1").read_text(encoding="utf-8")
        self.assertIn('"runtime.conf"', launcher)
        self.assertIn("wsl_project", launcher)
        self.assertIn("windows_python", launcher)
        self.assertNotIn("/home/samtec", launcher)
        self.assertIn("runtime.conf", setup)
        self.assertIn("-WslProject", setup)
        self.assertIn("BridgeHealthy()", launcher)
        self.assertIn("HttpRequestHeader.Authorization", launcher)
        self.assertIn('value.TrimEnd(\'/\') == "http://127.0.0.1:8787"', launcher)
        self.assertIn("Get-CimInstance Win32_Process", setup)
        self.assertIn("Unregister-ScheduledTask", setup)
        self.assertIn("sys.version_info >= (3, 11)", setup)
        self.assertIn("127.0.0.1', 'localhost", setup)
        self.assertIn("Set-NexoPrivateFileAcl -Path $tokenPath", setup)
        self.assertNotIn("icacls", setup.casefold())
        self.assertNotIn('Replace("\\\\", "\\\\\\\\")', (ROOT / "installer/windows/NexoSetup.cs").read_text(encoding="utf-8"))

    def test_private_token_acl_is_applied_and_verified_natively(self):
        powershell = shutil.which("powershell.exe")
        wslpath = shutil.which("wslpath")
        if not powershell or not wslpath:
            self.skipTest("Windows PowerShell interop is unavailable")
        module_windows = subprocess.check_output(
            [wslpath, "-w", str(ROOT / "scripts/installer_security.ps1")],
            text=True,
        ).strip()
        escaped_module = module_windows.replace("'", "''")
        command = (
            "$ErrorActionPreference='Stop'; "
            f". '{escaped_module}'; "
            "$target=Join-Path ([IO.Path]::GetTempPath()) "
            "('nexo-acl-'+[guid]::NewGuid().ToString('N')); "
            "try { "
            "[IO.File]::WriteAllText($target,'test'); "
            "$acl=Set-NexoPrivateFileAcl -Path $target; "
            "[ordered]@{protected=$acl.AreAccessRulesProtected;rules=@($acl.Access).Count}"
            "| ConvertTo-Json -Compress "
            "} finally { Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue }"
        )
        result = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            timeout=30,
        )
        output = result.stdout.decode("utf-8", errors="replace")
        error = result.stderr.decode("utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, error)
        match = re.search(rb"\{.*\}\s*$", result.stdout, flags=re.S)
        self.assertIsNotNone(match, output)
        acl = json.loads(match.group(0).decode("utf-8"))
        self.assertTrue(acl["protected"])
        self.assertEqual(acl["rules"], 1)

    def test_aec_installer_supports_ubuntu_variants_and_custom_paths(self):
        script = (ROOT / "scripts/install_pulse_aec.sh").read_text(encoding="utf-8")
        self.assertIn("apt-cache show liborc-0.4-0t64", script)
        self.assertIn("apt-cache show liborc-0.4-0", script)
        self.assertIn('s|%h/nexo-actions|$NEXO_REPO_ROOT|g', script)
        self.assertIn('s|%h/.venvs/ovos|$NEXO_VENV_PATH|g', script)

    def test_existing_distro_user_is_discovered_before_defaulting_to_windows_user(self):
        windows = (ROOT / "installer/windows/Install-Nexo.ps1").read_text(encoding="utf-8")
        discovery = windows.index("'id -un'")
        user_prompt = windows.index("Read-Default 'Usuário Linux do Nexo'")
        self.assertLess(discovery, user_prompt)
        self.assertIn("Get-CimInstance Win32_Process", windows)
        self.assertIn("zz-nexo-pulse-aec.conf", windows)

    def test_bash_installers_parse(self):
        if not shutil.which("bash"):
            self.skipTest("bash is unavailable")
        for relative in ("installer/wsl/bootstrap-root.sh", "installer/wsl/install-nexo.sh"):
            result = subprocess.run(
                ["bash", "-n", str(ROOT / relative)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_dry_run_is_safe_and_machine_readable(self):
        powershell = shutil.which("powershell.exe")
        wslpath = shutil.which("wslpath")
        if not powershell or not wslpath:
            self.skipTest("Windows PowerShell interop is unavailable")
        with tempfile.TemporaryDirectory(prefix="nexo-installer-test-") as temporary:
            script_windows = subprocess.check_output(
                [wslpath, "-w", str(ROOT / "installer/windows/Install-Nexo.ps1")],
                text=True,
            ).strip()
            local_app_data = subprocess.check_output(
                [wslpath, "-w", temporary], text=True
            ).strip()
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = local_app_data
            command = [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_windows,
                "-DryRun",
                "-NonInteractive",
                "-Mode",
                "Install",
                "-RepositoryUrl",
                "https://github.com/example/nexo-actions.git",
                "-Distro",
                "Ubuntu",
                "-WslUser",
                "nexo",
                "-InstallPath",
                "/home/nexo/nexo-actions",
                "-SourceRef",
                "0123456789abcdef0123456789abcdef01234567",
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=30,
                env=environment,
            )
            output = result.stdout.decode("utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            match = re.search(rb"\{\s*\"ok\".*\}\s*$", result.stdout, flags=re.S)
            self.assertIsNotNone(match, output)
            plan = json.loads(match.group(0).decode("utf-8"))
            self.assertTrue(plan["ok"])
            self.assertTrue(plan["dry_run"])
            self.assertEqual(plan["source_ref"], "0123456789abcdef0123456789abcdef01234567")
            self.assertFalse(plan["local_fallback_enabled"])


if __name__ == "__main__":
    unittest.main()
