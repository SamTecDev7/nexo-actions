import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nexo_actions import TOOL_REGISTRY
from nexo_actions.agent.confirmations import ConfirmationManager, confirmation_prompt
from nexo_actions.browser_tools import open_website, validate_url
from nexo_actions.file_tools import (
    _assert_deletable,
    _resolve_path,
    _safe_filename,
    create_text_file,
    find_files,
    read_text_file,
    replace_text_in_file,
    rewrite_text_file,
)
from nexo_actions.config import windows_to_wsl
from nexo_actions.skill import NexoActionsSkill


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class BrowserToolTests(unittest.TestCase):
    def test_url_validation_normalizes_domains(self):
        self.assertEqual(validate_url("clubenvios.com.br"), "https://clubenvios.com.br")
        self.assertEqual(validate_url("https://Google.com/a?q=1"), "https://google.com/a?q=1")

    def test_url_validation_blocks_unsafe_schemes_and_credentials(self):
        with self.assertRaises(ValueError):
            validate_url("file:///etc/passwd")
        with self.assertRaises(ValueError):
            validate_url("https://user:secret@example.com")
        with self.assertRaises(ValueError):
            validate_url("not a domain")

    @patch("nexo_actions.browser_tools._launch_url")
    @patch("nexo_actions.browser_tools.load_config")
    def test_site_aliases_are_configurable(self, load_config, launch):
        load_config.return_value = {"site_aliases": {"Minha Empresa": "https://example.com"}}
        result = open_website("minha empresa")
        self.assertEqual(result["url"], "https://example.com")
        self.assertIsNotNone(result["matched_alias"])
        launch.assert_called_once()

    @patch("nexo_actions.browser_tools._launch_url")
    @patch("nexo_actions.browser_tools.load_config")
    def test_unknown_site_name_becomes_search_not_invented_domain(self, load_config, launch):
        load_config.return_value = {"site_aliases": {}}
        result = open_website("Empresa Incerta")
        self.assertEqual(result["fallback"], "web_search")
        self.assertIn("google.com/search", result["url"])


class FileToolTests(unittest.TestCase):
    def test_wsl_unc_and_alias_child_paths_are_resolved(self):
        self.assertEqual(
            windows_to_wsl(r"\\wsl.localhost\Ubuntu\home\samtec\nexo-actions"),
            Path("/home/samtec/nexo-actions"),
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("nexo_actions.file_tools.known_directories", return_value={"downloads": str(root)}):
                self.assertEqual(_resolve_path("downloads/formulario.html"), root / "formulario.html")

    def test_spoken_filename_resolves_accents_and_spacing_safely(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = root / "formulario.html"
            expected.write_text("<form></form>", encoding="utf-8")
            with patch("nexo_actions.file_tools.known_directories", return_value={"downloads": str(root)}):
                self.assertEqual(
                    _resolve_path("downloads/formulário .html", must_exist=True),
                    expected,
                )

    def test_windows_reserved_names_are_rejected(self):
        for name in ("CON.txt", "nul", "../escape.txt", "bad?.txt"):
            with self.assertRaises(ValueError):
                _safe_filename(name)

    def test_project_files_are_protected_from_deletion(self):
        project_file = Path(__file__).resolve().parents[1] / "README.md"
        with patch("nexo_actions.file_tools.known_directories", return_value={}):
            with self.assertRaises(PermissionError):
                _assert_deletable(project_file, expect_directory=False)

    def test_find_files_by_extension_and_recency(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "old.pdf").write_bytes(b"old")
            (root / "note.txt").write_text("note", encoding="utf-8")
            (root / "new.pdf").write_bytes(b"new")
            with patch("nexo_actions.file_tools.wsl_to_windows", side_effect=lambda value: str(value)):
                result = find_files(extension="pdf", recent=True, root_override=root)
            self.assertEqual(result["found"], 2)
            self.assertTrue(all(item["name"].endswith(".pdf") for item in result["files"]))

    def test_find_files_prefers_the_named_folder_over_nested_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nested = root / "outro-projeto"
            nested.mkdir()
            (root / "style.css").write_text("body {}", encoding="utf-8")
            (nested / "style.css").write_text("body {}", encoding="utf-8")
            with patch("nexo_actions.file_tools.wsl_to_windows", side_effect=lambda value: str(value)):
                result = find_files(query="style.css", root_override=root)
            self.assertEqual(result["files"][0]["path"], str(root / "style.css"))

    @patch("nexo_actions.file_tools.wsl_to_windows", side_effect=lambda value: str(value))
    @patch("nexo_actions.file_tools._resolve_path")
    def test_create_text_file_does_not_overwrite_by_default(self, resolve, _convert):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolve.return_value = root
            first = create_text_file("downloads", "nota.txt", "olá", open_after=False)
            second = create_text_file("downloads", "nota.txt", "novo", open_after=False)
            self.assertTrue(first["ok"])
            self.assertTrue(second["conflict"])
            self.assertEqual((root / "nota.txt").read_text(encoding="utf-8"), "olá")

    def test_code_can_be_read_and_edited_with_version_check_and_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "formulario.html"
            target.write_text("<html><body><form></form></body></html>", encoding="utf-8")
            backup_root = root / "backups"
            with (
                patch("nexo_actions.file_tools.known_directories", return_value={"downloads": str(root)}),
                patch("nexo_actions.file_tools.wsl_to_windows", side_effect=lambda value: str(value)),
                patch("nexo_actions.file_tools.BACKUP_ROOT", backup_root),
            ):
                read = read_text_file("downloads/formulario.html")
                self.assertIn("<form>", read["content"])
                changed = replace_text_in_file(
                    "downloads/formulario.html",
                    "</head>",
                    '<link rel="stylesheet" href="styles.css"></head>',
                    read["sha256"],
                )
                self.assertFalse(changed["ok"])
                self.assertEqual(changed["reason"], "text_not_found")
                changed = replace_text_in_file(
                    "downloads/formulario.html",
                    "<body>",
                    '<body class="ready">',
                    read["sha256"],
                )
                self.assertTrue(changed["ok"])
                self.assertTrue(Path(changed["backup_path"]).is_file())
                conflict = rewrite_text_file(
                    "downloads/formulario.html", "conteúdo antigo", read["sha256"]
                )
                self.assertTrue(conflict["conflict"])
                self.assertIn('class="ready"', target.read_text(encoding="utf-8"))

    def test_sensitive_or_unknown_text_files_are_not_exposed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secret = root / ".env"
            secret.write_text("TOKEN=secret", encoding="utf-8")
            binary = root / "program.exe"
            binary.write_bytes(b"MZ")
            with patch("nexo_actions.file_tools.known_directories", return_value={"profile": str(root)}):
                with self.assertRaises(PermissionError):
                    read_text_file(str(secret))
                with self.assertRaises(ValueError):
                    read_text_file(str(binary))


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.manager = ConfirmationManager(ttl_seconds=30, clock=self.clock)

    def test_yes_consumes_pending_action_once(self):
        self.manager.request("move_file", {"source": "a", "destination": "b"}, "continuar?")
        decision, item = self.manager.consume("sim, pode fazer")
        self.assertTrue(decision.approved)
        self.assertEqual(item["tool_name"], "move_file")
        self.assertFalse(self.manager.pending)

    def test_no_cancels_and_unrelated_text_does_not_consume(self):
        self.manager.request("window_action", {"action": "close"}, "continuar?")
        decision, _item = self.manager.consume("talvez")
        self.assertFalse(decision.understood)
        self.assertTrue(self.manager.pending)
        decision, _item = self.manager.consume("não, cancela")
        self.assertTrue(decision.cancelled)

    def test_confirmation_expires(self):
        self.manager.request("move_file", {}, "continuar?")
        self.clock.now = 31
        self.assertFalse(self.manager.pending)

    def test_permanent_delete_requires_exact_strong_phrase(self):
        self.manager.request(
            "delete_file",
            {"path": "nota.txt", "permanent": True},
            "confirmar?",
            strength="strong",
        )
        decision, _item = self.manager.consume("sim, pode fazer")
        self.assertFalse(decision.understood)
        self.assertTrue(self.manager.pending)
        decision, item = self.manager.consume("confirmo exclusão permanente")
        self.assertTrue(decision.approved)
        self.assertEqual(item["strength"], "strong")

    def test_strong_confirmation_can_still_be_cancelled(self):
        self.manager.request("delete_folder", {}, "confirmar?", strength="strong")
        decision, _item = self.manager.consume("não, cancela")
        self.assertTrue(decision.cancelled)

    def test_close_window_prompt_mentions_unsaved_work(self):
        prompt = confirmation_prompt("window_action", {"action": "close", "query": "VS Code"})
        self.assertIn("não salvas", prompt)

    def test_email_confirmation_identifies_destination(self):
        prompt = confirmation_prompt(
            "email_send",
            {"to": "ana@example.com", "subject": "Reunião"},
        )
        self.assertIn("ana@example.com", prompt)
        self.assertIn("Reunião", prompt)


class Phase2RegistryTests(unittest.TestCase):
    def test_new_domains_are_registered_with_risk_metadata(self):
        self.assertEqual(len(TOOL_REGISTRY.names()), 61)
        for name in (
            "open_website", "find_files", "focus_window", "create_text_file",
            "read_text_file", "rewrite_text_file", "replace_text_in_file",
        ):
            self.assertIn(name, TOOL_REGISTRY.names())
        self.assertTrue(TOOL_REGISTRY.get("move_file").requires_confirmation)
        self.assertTrue(TOOL_REGISTRY.get("delete_file").requires_confirmation)
        self.assertTrue(TOOL_REGISTRY.get("power_action").requires_confirmation)
        self.assertEqual(TOOL_REGISTRY.get("type_text").log_policy, "metadata_only")
        self.assertEqual(TOOL_REGISTRY.get("rewrite_text_file").log_policy, "metadata_only")

    def test_fast_router_handles_urls_folders_and_window_followups(self):
        skill = SimpleNamespace(
            _normalize=NexoActionsSkill._normalize,
            nexo_config={"site_aliases": {"club envios": "https://clubenvios.com.br"}},
            conversation=SimpleNamespace(references={"last_app": "Google Chrome"}),
        )
        route = NexoActionsSkill._fast_route_tool
        self.assertEqual(route(skill, "abre clubenvios.com.br")["name"], "open_url")
        self.assertEqual(route(skill, "abre o site do Club Envios")["name"], "open_website")
        self.assertEqual(route(skill, "abre Downloads"), {"name": "open_folder", "arguments": {"path": "downloads"}})
        followup = route(skill, "agora maximiza ele")
        self.assertEqual(followup["arguments"]["query"], "Google Chrome")
        self.assertEqual(route(skill, "troca de janela")["name"], "switch_window")
        self.assertEqual(
            route(skill, "Leia o formulário .html em Downloads"),
            {
                "name": "read_text_file",
                "arguments": {"path": "downloads/formulário.html"},
            },
        )

    def test_conditional_risk_requires_confirmation(self):
        skill = SimpleNamespace(nexo_config={"safety": {"confirm_medium_risk": False}})
        check = NexoActionsSkill._needs_confirmation
        self.assertTrue(check(skill, TOOL_REGISTRY.get("window_action"), {"action": "close"}))
        self.assertFalse(check(skill, TOOL_REGISTRY.get("window_action"), {"action": "maximize"}))
        self.assertTrue(check(skill, TOOL_REGISTRY.get("create_text_file"), {"overwrite": True}))


if __name__ == "__main__":
    unittest.main()
