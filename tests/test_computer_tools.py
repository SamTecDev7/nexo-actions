import unittest
from unittest.mock import patch

from nexo_actions.computer_tools import computer_action, observe_desktop
from nexo_actions.tools import TOOL_REGISTRY
from nexo_actions.windows_bridge.observations import ObservationRejected, ObservationStore


class FakeClock:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        return self.value


class ObservationStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = ObservationStore(
            ttl_seconds=10,
            clock=self.clock,
            wall_clock=lambda: 1_700_000_000.0,
        )
        self.bounds = {"left": 10, "top": 20, "width": 800, "height": 600}

    def create(self):
        return self.store.create(
            hwnd=42,
            window_bounds=self.bounds,
            capture_bounds=self.bounds,
            image_width=800,
            image_height=600,
            scope="active_window",
        )

    def test_observation_is_bound_to_focus_geometry_and_is_one_shot(self):
        item = self.create()
        accepted = self.store.validate(
            item["observation_id"], current_hwnd=42, current_bounds=self.bounds
        )
        self.assertEqual(accepted["hwnd"], 42)
        with self.assertRaisesRegex(ObservationRejected, "já foi utilizada"):
            self.store.validate(
                item["observation_id"], current_hwnd=42, current_bounds=self.bounds
            )

    def test_focus_and_geometry_changes_are_rejected_without_consuming(self):
        item = self.create()
        with self.assertRaisesRegex(ObservationRejected, "janela ativa mudou"):
            self.store.validate(
                item["observation_id"], current_hwnd=99, current_bounds=self.bounds
            )
        moved = {**self.bounds, "left": 11}
        with self.assertRaisesRegex(ObservationRejected, "posição ou tamanho"):
            self.store.validate(
                item["observation_id"], current_hwnd=42, current_bounds=moved
            )

    def test_expired_observation_is_rejected(self):
        item = self.create()
        self.clock.value += 11
        with self.assertRaisesRegex(ObservationRejected, "ficou antiga"):
            self.store.validate(
                item["observation_id"], current_hwnd=42, current_bounds=self.bounds
            )


class ComputerToolTests(unittest.TestCase):
    @patch("nexo_actions.computer_tools._client")
    def test_observe_desktop_forwards_bounded_capture(self, client_factory):
        client_factory.return_value.call.return_value = {
            "ok": True,
            "observation_id": "obs",
            "image_base64": "png",
        }
        result = observe_desktop("active_window", 1024, 640)
        self.assertTrue(result["ok"])
        client_factory.return_value.call.assert_called_once_with(
            "observe_desktop",
            {"scope": "active_window", "max_width": 1024, "max_height": 640},
        )

    @patch("nexo_actions.computer_tools._client")
    def test_computer_action_does_not_echo_sensitive_text(self, client_factory):
        client_factory.return_value.call.return_value = {
            "ok": True,
            "verified": True,
            "characters": 12,
        }
        result = computer_action(
            "type_text",
            "obs",
            text="segredo local",
            sensitive=True,
            confirmation_reason="enviar uma mensagem",
            authorization="explicit_now",
            authorization_evidence="envie agora",
            effect_kind="communication",
        )
        self.assertNotIn("text", result)
        params = client_factory.return_value.call.call_args.args[1]
        self.assertEqual(params["text"], "segredo local")
        self.assertNotIn("sensitive", params)
        self.assertNotIn("confirmation_reason", params)
        self.assertNotIn("authorization", params)
        self.assertNotIn("authorization_evidence", params)
        self.assertNotIn("effect_kind", params)

    def test_computer_action_schema_restricts_semantic_authorization_metadata(self):
        spec = TOOL_REGISTRY.get("computer_action")
        TOOL_REGISTRY.validate_arguments(spec, {
            "action": "click",
            "authorization": "explicit_now",
            "effect_kind": "communication",
        })
        with self.assertRaises(ValueError):
            TOOL_REGISTRY.validate_arguments(spec, {
                "action": "click",
                "authorization": "inferred_from_screen",
                "effect_kind": "communication",
            })
        with self.assertRaises(ValueError):
            TOOL_REGISTRY.validate_arguments(spec, {
                "action": "click",
                "authorization": "explicit_now",
                "effect_kind": "terminal_unknown",
            })

    def test_registry_never_logs_screenshot_or_typed_text(self):
        observe = TOOL_REGISTRY.get("observe_desktop")
        action = TOOL_REGISTRY.get("computer_action")
        self.assertEqual(observe.log_policy, "none")
        self.assertEqual(action.log_policy, "metadata_only")


if __name__ == "__main__":
    unittest.main()
