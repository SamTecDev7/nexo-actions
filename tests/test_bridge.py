import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from nexo_actions.windows_bridge.client import BridgeError, WindowsBridgeClient


class WindowsBridgeClientTests(unittest.TestCase):
    def test_http_rpc_error_is_not_reported_as_unavailable(self):
        payload = json.dumps({
            "ok": False,
            "error": {"type": "LookupError", "message": "Janela não encontrada"},
        }).encode("utf-8")
        error = urllib.error.HTTPError(
            "http://127.0.0.1:8765/rpc", 400, "Bad Request", {}, io.BytesIO(payload)
        )
        client = WindowsBridgeClient(token="x" * 32)
        with patch("nexo_actions.windows_bridge.client.urllib.request.urlopen", side_effect=error):
            with patch.object(client, "_call_via_windows_proxy") as proxy:
                with self.assertRaisesRegex(BridgeError, "Janela não encontrada"):
                    client.call("focus_window", {"query": "inexistente"})
                proxy.assert_not_called()

    def test_transport_failure_can_use_windows_proxy(self):
        client = WindowsBridgeClient(token="x" * 32)
        offline = urllib.error.URLError("refused")
        with patch("nexo_actions.windows_bridge.client.urllib.request.urlopen", side_effect=offline):
            with patch.object(
                client,
                "_call_via_windows_proxy",
                return_value={"ok": True, "result": {"status": "ok"}},
            ):
                self.assertEqual(client.call("health")["status"], "ok")


if __name__ == "__main__":
    unittest.main()
