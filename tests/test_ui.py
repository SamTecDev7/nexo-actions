import json
import threading
import unittest
import urllib.request

from nexo_actions.ui.server import NexoUIBridge, StateHub, create_server


class FakeBus:
    def __init__(self):
        self.emitted = []

    def emit(self, message):
        self.emitted.append(message)


class StateHubTests(unittest.TestCase):
    def test_state_is_versioned_bounded_and_validated(self):
        hub = StateHub()
        initial = hub.snapshot()
        changed = hub.update({"state": "speaking", "text": "x" * 900, "progress": 140})
        self.assertGreater(changed["version"], initial["version"])
        self.assertEqual(changed["state"], "speaking")
        self.assertEqual(len(changed["text"]), 600)
        self.assertEqual(changed["progress"], 100)
        self.assertEqual(hub.update({"state": "unknown"})["state"], "error")

    def test_new_state_does_not_keep_stale_presentation_fields(self):
        hub = StateHub()
        hub.update({"state": "speaking", "label": "Falando", "text": "Olá", "progress": 42})
        changed = hub.update({"state": "idle"})
        self.assertEqual(changed["label"], "")
        self.assertEqual(changed["text"], "")
        self.assertIsNone(changed["progress"])

    def test_typed_utterance_enters_local_bus(self):
        hub = StateHub()
        bus = FakeBus()
        bridge = NexoUIBridge(hub, client=bus)
        bridge.emit_utterance("  abre   Downloads  ")
        self.assertEqual(bus.emitted[0].msg_type, "recognizer_loop:utterance")
        self.assertEqual(bus.emitted[0].data["utterances"], ["abre Downloads"])
        self.assertEqual(hub.snapshot()["state"], "thinking")

    def test_record_end_leaves_listening_state_immediately(self):
        hub = StateHub()
        bridge = NexoUIBridge(hub, client=FakeBus())
        bridge._on_listening()
        self.assertEqual(hub.snapshot()["state"], "listening")
        bridge._on_processing()
        state = hub.snapshot()
        self.assertEqual(state["state"], "thinking")
        self.assertEqual(state["label"], "Processando")

    def test_audio_end_never_forces_an_active_task_to_idle(self):
        hub = StateHub()
        bridge = NexoUIBridge(hub, client=FakeBus())
        hub.update({"state": "acting", "label": "Executando", "tool": "agent_run"})
        bridge._on_speaking()
        self.assertEqual(hub.snapshot()["state"], "acting")
        self.assertTrue(hub.snapshot()["voice_active"])
        bridge._on_audio_end()
        state = hub.snapshot()
        self.assertEqual(state["state"], "acting")
        self.assertFalse(state["voice_active"])


class UIServerTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server("127.0.0.1", 0, connect_bus=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_static_interface_and_state_api_are_local(self):
        with urllib.request.urlopen(self.base + "/", timeout=3) as response:
            html = response.read().decode("utf-8")
            self.assertIn("NEXO", html)
            self.assertIn("hologram", html)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        with urllib.request.urlopen(self.base + "/api/state", timeout=3) as response:
            state = json.loads(response.read())
            self.assertEqual(state["state"], "idle")

    def test_admin_first_run_and_security_headers(self):
        with urllib.request.urlopen(self.base + "/admin", timeout=3) as response:
            html = response.read().decode("utf-8")
            self.assertIn("Núcleo ADM", html)
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        with urllib.request.urlopen(self.base + "/api/admin/status", timeout=3) as response:
            status = json.loads(response.read())
            self.assertTrue(status["secure_local"])
            self.assertFalse(status["authenticated"])

    def test_non_loopback_bind_is_rejected(self):
        with self.assertRaises(ValueError):
            create_server("0.0.0.0", 0, connect_bus=False)


if __name__ == "__main__":
    unittest.main()
