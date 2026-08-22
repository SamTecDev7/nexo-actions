import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nexo_actions import TOOL_REGISTRY, TOOLS
from nexo_actions.audio_tools import change_volume, set_volume
from nexo_actions.jobs import JobManager
from nexo_actions.process_control_tools import PROTECTED
from nexo_actions.storage_tools import (
    clear_storage_cache,
    find_large_files,
    find_large_folders,
)
from nexo_actions.tool_registry import ToolRegistry, ToolSpec
from nexo_actions.tools import _score_app


def wait_for_state(manager, job_id, states, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job and job["state"] in states:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} não chegou a {states}")


class ToolRegistryTests(unittest.TestCase):
    def test_default_registry_generates_unique_ollama_schemas(self):
        names = [item["function"]["name"] for item in TOOLS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names, TOOL_REGISTRY.names())
        self.assertEqual(len(names), 61)

    def test_every_tool_has_operational_metadata(self):
        for spec in TOOL_REGISTRY.specs():
            self.assertGreater(spec.timeout, 0)
            self.assertTrue(spec.category)
            self.assertIn(spec.risk_level, {"read_only", "low", "medium", "high"})
            self.assertIn(spec.log_policy, {"standard", "metadata_only", "none"})
            self.assertIsInstance(spec.requires_confirmation, bool)
            self.assertIsInstance(spec.long_running, bool)

    def test_argument_validation_and_execution(self):
        spec = ToolSpec(
            name="echo_level",
            description="test",
            parameters={
                "type": "object",
                "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 10}},
                "required": ["level"],
                "additionalProperties": False,
            },
            handler=lambda level: {"level": level},
        )
        registry = ToolRegistry([spec])
        self.assertEqual(registry.execute("echo_level", {"level": 4}), {"level": 4})
        with self.assertRaises(ValueError):
            registry.execute("echo_level", {"level": 11})
        with self.assertRaises(ValueError):
            registry.execute("echo_level", {"level": 4, "extra": True})
        with self.assertRaises(ValueError):
            registry.execute("echo_level", {})

    def test_category_filter_reduces_schema_catalog(self):
        audio = TOOL_REGISTRY.schemas(categories={"audio"})
        self.assertEqual(
            {item["function"]["name"] for item in audio},
            {
                "get_volume", "set_volume", "change_volume", "set_mute",
                "audio_input_status", "repair_audio_input",
            },
        )


class JobManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = JobManager(max_workers=2, retention_seconds=0)

    def tearDown(self):
        self.manager.shutdown(wait=False)

    def test_completion_and_progress(self):
        progress = []

        def work(job_context=None):
            job_context.report_progress(50, "metade", {"items": 2})
            return {"ok": True}

        submission = self.manager.submit(
            "work", work, pass_context=True, on_progress=progress.append
        )
        job = wait_for_state(self.manager, submission.job_id, {"completed"})
        self.assertEqual(job["result"], {"ok": True})
        self.assertEqual(progress[0]["progress"], 50)

    def test_duplicate_active_job_is_reused(self):
        release = threading.Event()

        def blocked():
            release.wait(1)

        first = self.manager.submit("scan", blocked, dedupe_key="same")
        second = self.manager.submit("scan", blocked, dedupe_key="same")
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.job_id, second.job_id)
        release.set()
        wait_for_state(self.manager, first.job_id, {"completed"})

    def test_timeout_marks_failed_and_requests_cooperative_cancel(self):
        def slow(job_context=None):
            while not job_context.cancelled:
                time.sleep(0.005)

        submission = self.manager.submit(
            "slow", slow, timeout=0.05, pass_context=True
        )
        job = wait_for_state(self.manager, submission.job_id, {"failed"})
        self.assertEqual(job["error_type"], "TimeoutError")

    def test_cancel_running_job(self):
        def slow(job_context=None):
            while not job_context.cancelled:
                time.sleep(0.005)

        submission = self.manager.submit("slow", slow, pass_context=True)
        self.assertTrue(self.manager.cancel(submission.job_id))
        job = self.manager.get(submission.job_id)
        self.assertEqual(job["state"], "cancelled")


class StorageScannerTests(unittest.TestCase):
    def setUp(self):
        clear_storage_cache()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "alpha").mkdir()
        (self.root / "alpha" / "nested").mkdir()
        (self.root / "beta").mkdir()
        (self.root / "alpha" / "a.bin").write_bytes(b"a" * (1024 ** 2))
        (self.root / "alpha" / "nested" / "b.bin").write_bytes(b"b" * (2 * 1024 ** 2))
        (self.root / "beta" / "c.bin").write_bytes(b"c" * 512)

    def tearDown(self):
        self.temp.cleanup()
        clear_storage_cache()

    def test_single_scan_aggregates_each_top_level_folder(self):
        result = find_large_folders(
            "downloads", 10, root_override=self.root, use_cache=False
        )
        sizes = {item["name"]: item["size_bytes"] for item in result["folders"]}
        self.assertTrue(result["complete"])
        self.assertEqual(sizes, {"alpha": 3 * 1024 ** 2, "beta": 512})
        self.assertEqual(result["backend"], "python")

    def test_large_files_uses_bounded_sorted_results(self):
        result = find_large_files(
            "downloads",
            2,
            1,
            root_override=self.root,
            use_cache=False,
        )
        self.assertEqual([item["name"] for item in result["files"]], ["b.bin", "a.bin"])

    def test_cache_is_reused(self):
        first = find_large_folders("downloads", 10, root_override=self.root)
        second = find_large_folders("downloads", 10, root_override=self.root)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])

    def test_timeout_returns_partial_instead_of_raising(self):
        result = find_large_folders(
            "profile",
            10,
            root_override=self.root,
            timeout_seconds=0,
            use_cache=False,
        )
        self.assertTrue(result["partial"])
        self.assertTrue(result["timed_out"])

    def test_cancellation_returns_partial(self):
        cancel = threading.Event()
        cancel.set()
        result = find_large_folders(
            "profile",
            10,
            root_override=self.root,
            cancel_event=cancel,
            use_cache=False,
        )
        self.assertTrue(result["partial"])
        self.assertTrue(result["cancelled"])

    def test_progress_callback_is_supported(self):
        updates = []
        find_large_folders(
            "downloads",
            10,
            root_override=self.root,
            progress_after_seconds=0,
            progress_callback=updates.append,
            use_cache=False,
        )
        self.assertTrue(updates)
        self.assertIn("files_scanned", updates[0])


class CompatibilityTests(unittest.TestCase):
    def test_fuzzy_app_score_prefers_exact_and_contains(self):
        self.assertEqual(_score_app("Visual Studio Code", "Visual Studio Code"), 100)
        self.assertGreater(_score_app("Chrome", "Google Chrome"), 70)

    def test_critical_processes_remain_protected(self):
        for name in {"system", "lsass", "svchost", "vmmem", "msmpeng"}:
            self.assertIn(name, PROTECTED)

    @patch("nexo_actions.audio_tools._run")
    def test_volume_arguments_are_clamped(self, run):
        run.return_value = {"ok": True}
        set_volume(150)
        change_volume(-500)
        self.assertEqual(run.call_args_list[0].args, ("set", 100))
        self.assertEqual(run.call_args_list[1].args, ("change", -100))


if __name__ == "__main__":
    unittest.main()
