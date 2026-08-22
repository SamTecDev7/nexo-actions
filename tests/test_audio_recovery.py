import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from nexo_actions.audio_recovery import (
    AEC_SOURCE,
    ISOLATED_SERVER,
    MIN_STREAM_BYTES,
    UPSTREAM_SOURCE,
    AudioRecovery,
)


class AudioRecoveryTests(unittest.TestCase):
    def recovery(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        return AudioRecovery(
            config_path=root / "config.json",
            status_path=root / "health.json",
            lock_path=root / "repair.lock",
        )

    def test_passive_health_never_opens_a_recording_stream(self):
        recovery = self.recovery()
        recovery._source_names = MagicMock(side_effect=[
            ({UPSTREAM_SOURCE}, ""), ({AEC_SOURCE}, ""),
        ])
        recovery._probe_source = MagicMock()
        recovery._gain_percent = MagicMock(return_value=135)

        result = recovery.health(probe_audio=False)

        self.assertTrue(result["healthy"])
        self.assertEqual(result["probe_mode"], "passive")
        recovery._probe_source.assert_not_called()

    def test_active_test_probes_only_the_final_aec_source(self):
        recovery = self.recovery()
        recovery._source_names = MagicMock(side_effect=[
            ({UPSTREAM_SOURCE}, ""), ({AEC_SOURCE}, ""),
        ])
        recovery._probe_source = MagicMock(return_value=(MIN_STREAM_BYTES, ""))
        recovery._gain_percent = MagicMock(return_value=135)

        result = recovery.health(probe_audio=True)

        self.assertTrue(result["healthy"])
        self.assertEqual(result["upstream"]["bytes"], 0)
        recovery._probe_source.assert_called_once_with(ISOLATED_SERVER, AEC_SOURCE)

    def test_normal_repair_does_not_recycle_wslg_when_source_exists(self):
        recovery = self.recovery()
        before = {
            "upstream": {"present": True},
            "aec": {"present": True},
        }
        after = {
            "ok": True,
            "healthy": True,
            "reason": "operational",
            "upstream": {"present": True},
            "aec": {"present": True},
        }
        recovery.health = MagicMock(side_effect=[before, after])
        recovery._recover_wslg = MagicMock()
        recovery._restart_stack = MagicMock()

        result = recovery.repair(reason="test")

        self.assertFalse(result["recovered_wslg"])
        recovery._recover_wslg.assert_not_called()
        self.assertEqual(
            [call.kwargs.get("probe_audio") for call in recovery.health.call_args_list],
            [False, False],
        )

    def test_forced_repair_recycles_wslg_once(self):
        recovery = self.recovery()
        before = {
            "upstream": {"present": True},
            "aec": {"present": True},
        }
        after = {
            "ok": True,
            "healthy": True,
            "reason": "operational",
            "upstream": {"present": True},
            "aec": {"present": True},
        }
        recovery.health = MagicMock(side_effect=[before, after])
        recovery._recover_wslg = MagicMock()
        recovery._restart_stack = MagicMock()

        result = recovery.repair(force_wslg=True, reason="test")

        self.assertTrue(result["recovered_wslg"])
        recovery._recover_wslg.assert_called_once_with()

    def test_absent_windows_microphone_does_not_stop_running_services(self):
        calls = []

        def runner(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(
                command, 0, stdout='{"ok":true,"available":false}\n', stderr=""
            )

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        recovery = AudioRecovery(
            config_path=root / "config.json",
            status_path=root / "health.json",
            lock_path=root / "repair.lock",
            runner=runner,
        )

        with self.assertRaisesRegex(RuntimeError, "Aguardando"):
            recovery._recover_wslg()

        self.assertEqual(len(calls), 1)
        self.assertIn("capture-status", calls[0])


if __name__ == "__main__":
    unittest.main()
