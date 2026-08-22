import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nexo_actions.config import DEFAULT_CONFIG, load_config
from nexo_actions.secrets import load_openai_api_key, openai_credential_status


class SecretTests(unittest.TestCase):
    def test_environment_takes_precedence_over_dotenv(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("OPENAI_API_KEY=file-test-value\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "environment-test-value"}):
                self.assertEqual(load_openai_api_key(path), "environment-test-value")

    def test_dotenv_parser_does_not_execute_shell_syntax(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text(
                "# local credential\nexport OPENAI_API_KEY='safe-test-value'\nIGNORED=$(whoami)\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_openai_api_key(path), "safe-test-value")

    def test_public_status_contains_no_secret(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "never-return-this-value"}):
            status = openai_credential_status()
        self.assertEqual(status, {
            "configured": True,
            "source": "environment",
            "secure_storage": True,
        })
        self.assertNotIn("never-return", repr(status))


class AIConfigTests(unittest.TestCase):
    def test_default_openai_profile_and_paused_local_fallback(self):
        ai = DEFAULT_CONFIG["ai"]
        self.assertEqual(ai["provider"], "openai")
        self.assertEqual(ai["fallback_provider"], "ollama")
        self.assertFalse(ai["fallback_enabled"])
        self.assertEqual(ai["profiles"]["openai"]["agent_model"], "gpt-5.6-sol")
        self.assertEqual(ai["profiles"]["openai"]["realtime_model"], "gpt-realtime-2.1")
        self.assertEqual(ai["profiles"]["openai"]["voice"], "marin")

    def test_legacy_ollama_config_is_deep_merged_with_ai_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text('{"ollama":{"model":"legacy-local"}}', encoding="utf-8")
            loaded = load_config(path, force=True)
        self.assertEqual(loaded["ollama"]["model"], "legacy-local")
        self.assertEqual(loaded["ai"]["provider"], "openai")
