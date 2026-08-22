import sqlite3
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from nexo_actions.memory import (
    MAX_SEARCH_LIMIT,
    MemorySizeError,
    MemoryStore,
    SCHEMA_VERSION,
    SensitiveMemoryError,
)


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "private" / "memory.sqlite3"
        self.store = MemoryStore(self.path)
        self.addCleanup(self.store.close)

    def test_database_is_private_versioned_wal_and_persistent(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                SCHEMA_VERSION,
            )
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "wal",
            )

        self.store.remember_preference(
            "tema_interface",
            {"modo": "escuro", "cor": "azul"},
        )
        self.store.close()

        reopened = MemoryStore(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(
            reopened.get_preference("TEMA_INTERFACE")["value"],
            {"modo": "escuro", "cor": "azul"},
        )

    def test_weak_unconfirmed_inference_is_not_persisted(self):
        persisted = self.store.remember_preference(
            "musica_favorita",
            "Estrelinha",
            source="inferred",
            confidence=0.72,
            confirmed=False,
        )

        self.assertFalse(persisted)
        self.assertIsNone(self.store.get_preference("musica_favorita"))

        self.assertTrue(self.store.remember_preference(
            "musica_favorita",
            "Estrelinha",
            source="inferred",
            confidence=0.91,
            confirmed=False,
        ))
        record = self.store.get_preference("musica_favorita")
        self.assertEqual(record["value"], "Estrelinha")
        self.assertFalse(record["confirmed"])

    def test_confirmed_preference_upserts_and_searches_literal_wildcards(self):
        self.store.remember_preference(
            "volume_geral",
            "50%",
            source="user",
            confidence=1,
            confirmed=True,
        )
        self.store.remember_preference("voz", "marin")
        self.store.remember_preference("voz", "cedar")
        self.store.remember_preference("bebida", "caf�")

        self.assertEqual(self.store.get_preference("voz")["value"], "cedar")
        self.assertEqual(
            [item["key"] for item in self.store.search_preferences("CAF�")],
            ["bebida"],
        )
        self.assertEqual(
            [item["key"] for item in self.store.search_preferences("%")],
            ["volume_geral"],
        )
        self.assertEqual(
            [item["key"] for item in self.store.search_preferences("_")],
            ["volume_geral"],
        )
        self.assertLessEqual(
            len(self.store.search_preferences("", limit=10_000)),
            MAX_SEARCH_LIMIT,
        )

    def test_forget_is_idempotent_and_store_close_is_idempotent(self):
        self.store.remember_preference("idioma", "pt-BR")

        self.assertTrue(self.store.forget_preference("idioma"))
        self.assertFalse(self.store.forget_preference("idioma"))
        self.assertIsNone(self.store.get_preference("idioma"))

        self.store.close()
        self.store.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.store.get_preference("idioma")

    def test_credentials_tokens_and_oversized_values_are_rejected(self):
        fake_key = "sk-" + ("not-a-real-key" * 3)
        with self.assertRaises(SensitiveMemoryError):
            self.store.remember_preference("openai_api_key", fake_key)
        with self.assertRaises(SensitiveMemoryError):
            self.store.remember_preference("nota", "Bearer " + ("x" * 32))
        with self.assertRaises(SensitiveMemoryError):
            self.store.record_task_receipt(
                "configure_provider",
                metadata={"access_token": "fake-token-value"},
            )
        with self.assertRaises(SensitiveMemoryError):
            self.store.record_task_receipt(
                "open_app",
                task_id="Bearer " + ("y" * 32),
            )
        with self.assertRaises(MemorySizeError):
            self.store.remember_preference("texto", "x" * 9_000)
        with self.assertRaises(TypeError):
            self.store.remember_preference(
                "inferida",
                "valor",
                confirmed="yes",
            )

        self.assertEqual(self.store.search_preferences(""), [])
        self.assertEqual(self.store.recent_task_receipts(), [])
        raw = self.path.read_bytes()
        self.assertNotIn(fake_key.encode("utf-8"), raw)

    def test_task_receipts_are_recent_filterable_bounded_metadata(self):
        first = self.store.record_task_receipt(
            "open_app",
            "completed",
            task_id="task-1",
            summary="Chrome aberto",
            target="chrome.exe",
            metadata={"tool": "open_app", "duration_ms": 120},
        )
        second = self.store.record_task_receipt(
            "set_volume",
            "failed",
            task_id="task-2",
            summary="Dispositivo indispon�vel",
            metadata={"tool": "set_volume"},
        )

        recent = self.store.recent_task_receipts(limit=2)
        self.assertEqual([item["task_id"] for item in recent], ["task-2", "task-1"])
        self.assertEqual(recent[1], first)
        self.assertEqual(recent[0], second)
        self.assertEqual(
            [item["task_id"] for item in self.store.recent_task_receipts(
                status="completed"
            )],
            ["task-1"],
        )
        with self.assertRaises(ValueError):
            self.store.recent_task_receipts(limit=0)

    def test_thread_safe_writes_share_one_connection(self):
        barrier = threading.Barrier(8)

        def write(index):
            barrier.wait(timeout=2)
            return self.store.remember_preference(
                f"preference_{index}",
                {"index": index},
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(write, range(8)))

        self.assertTrue(all(results))
        records = self.store.search_preferences("preference_", limit=20)
        self.assertEqual(len(records), 8)
        self.assertEqual(
            {record["value"]["index"] for record in records},
            set(range(8)),
        )

    def test_newer_schema_is_rejected_instead_of_being_overwritten(self):
        path = Path(self.folder.name) / "future.sqlite3"
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA user_version=999")

        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            MemoryStore(path)


if __name__ == "__main__":
    unittest.main()
