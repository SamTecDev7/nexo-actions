from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


artifacts = _load("ci_validate_artifacts", "scripts/ci_validate_artifacts.py")
repository = _load("ci_check_repository", "scripts/ci_check_repository.py")
versions = _load("ci_validate_version", "scripts/ci_validate_version.py")


class ArtifactValidationTests(unittest.TestCase):
    def test_accepts_nontrivial_pe_file_and_returns_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Nexo.exe"
            path.write_bytes(b"MZ" + (b"safe" * 5000))
            result = artifacts._validate_executable(path)
            self.assertEqual("Nexo.exe", result["name"])
            self.assertEqual(64, len(result["sha256"]))

    def test_rejects_embedded_openai_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "NexoSetup.exe"
            fake_key = b"sk-" + b"proj-" + (b"a" * 48)
            path.write_bytes(b"MZ" + (b"x" * 17000) + fake_key)
            with self.assertRaisesRegex(ValueError, "segredo"):
                artifacts._validate_executable(path)

    def test_rejects_secret_named_file_in_release_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text("OPENAI_API_KEY=nope", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sensível"):
                artifacts._validate_tree(root)

    def test_manifest_verification_binds_artifacts_to_source_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "ReleaseManifest.json"
            report = {
                "source_commit": "a" * 40,
                "artifacts": [
                    {"name": "Nexo.exe", "bytes": 20_000, "sha256": "b" * 64}
                ],
            }
            manifest.write_text(json.dumps(report), encoding="utf-8")
            artifacts._verify_manifest(manifest, report)
            changed = {
                "source_commit": "c" * 40,
                "artifacts": report["artifacts"],
            }
            with self.assertRaisesRegex(ValueError, "source_commit"):
                artifacts._verify_manifest(manifest, changed)


class RepositoryCredentialTests(unittest.TestCase):
    def test_allows_placeholder_env_example(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sample = root / ".env.example"
            sample.write_text("OPENAI_API_KEY=<sua-chave>\n", encoding="utf-8")
            repository.validate([sample], root)

    def test_rejects_real_key_in_text_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "config.txt"
            fake_key = "sk-" + "proj-" + ("a" * 48)
            source.write_text(
                "OPENAI_API" + "_KEY=" + fake_key + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "credencial"):
                repository.validate([source], root)

    def test_rejects_tracked_private_key_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            private_key = root / "release-signing.pfx"
            private_key.write_bytes(b"not-a-real-key")
            with self.assertRaisesRegex(ValueError, "nome sensível"):
                repository.validate([private_key], root)


class WorkflowContractTests(unittest.TestCase):
    def test_ci_builds_both_windows_artifacts_without_installing(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("-BuildOnly", workflow)
        self.assertIn("build_windows_installer.ps1", workflow)
        self.assertIn("-SourceRef $env:GITHUB_SHA", workflow)
        self.assertIn("source_ref_embedded", workflow)
        self.assertIn("ci_validate_installer_resources.ps1", workflow)
        self.assertIn("--require-installer", workflow)
        self.assertIn("bash -n installer/wsl/*.sh", workflow)
        self.assertIn("ci_validate_powershell.ps1", workflow)
        self.assertIn("tests.test_installer_simulation", workflow)
        self.assertIn("ubuntu-22.04", workflow)
        self.assertIn("ubuntu-24.04", workflow)
        self.assertIn("ci_test_windows_installer.ps1", workflow)
        self.assertNotIn("OPENAI_API_KEY", workflow)

    def test_windows_installer_ci_simulation_is_isolated_and_covers_all_modes(self):
        script = (ROOT / "scripts/ci_test_windows_installer.ps1").read_text(
            encoding="utf-8"
        )
        for mode in ("Install", "Repair", "Uninstall"):
            self.assertIn(f"'{mode}'", script)
        self.assertIn("[IO.Path]::GetTempPath()", script)
        self.assertIn("$env:LOCALAPPDATA = $temporaryRoot", script)
        self.assertIn("touched_user_runtime = $false", script)
        self.assertIn("install-path-boundary", script)
        self.assertIn("git-ref-traversal", script)

    def test_release_only_runs_for_version_tags_and_checks_version(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn('"v*.*.*"', workflow)
        self.assertIn("ci_validate_version.py", workflow)
        self.assertIn("needs: validate-tag", workflow)
        self.assertIn("--verify-manifest", workflow)
        self.assertIn("Refuse to overwrite", workflow)

    def test_version_validator_requires_exact_semver_tag(self):
        self.assertEqual("2.0.0", versions._tag_version("v2.0.0"))
        for invalid in ("2.0.0", "v2.0", "v2.0.0-rc1", "release-v2.0.0"):
            with self.subTest(tag=invalid):
                with self.assertRaises(ValueError):
                    versions._tag_version(invalid)


if __name__ == "__main__":
    unittest.main()
