import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexo_actions.memory import MemoryStore, SensitiveMemoryError
from nexo_actions import memory_tools


class MemoryToolTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.tempdir.name) / "memory.sqlite3")
        self.patcher = patch.object(memory_tools, "_STORE", self.store)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.store.close()
        self.tempdir.cleanup()

    def test_explicit_preference_can_be_remembered_and_recalled(self):
        result = memory_tools.remember_preference("musica_favorita", "Estrelinha")
        self.assertTrue(result["stored"])

        recalled = memory_tools.recall_preferences("música", 5)
        self.assertEqual(recalled["count"], 1)
        self.assertEqual(recalled["preferences"][0]["value"], "Estrelinha")

    def test_credential_like_preference_is_refused(self):
        with self.assertRaises(SensitiveMemoryError):
            memory_tools.remember_preference("api_key", "sk-example-secret-value")

    def test_forget_is_idempotent(self):
        memory_tools.remember_preference("app_favorito", "Chrome")
        self.assertTrue(memory_tools.forget_preference("app_favorito")["removed"])
        self.assertFalse(memory_tools.forget_preference("app_favorito")["removed"])

    def test_receipt_contains_only_execution_metadata(self):
        memory_tools.record_agent_receipt(
            task_id="job-1",
            status="completed",
            steps=({"name": "open_app", "arguments": {"app_name": "Chrome"}},),
            verified=True,
        )
        receipt = self.store.recent_task_receipts(1)[0]
        self.assertEqual(receipt["action"], "agent_task")
        self.assertEqual(receipt["metadata"]["tools"], ["open_app"])
        self.assertNotIn("arguments", str(receipt))


if __name__ == "__main__":
    unittest.main()
