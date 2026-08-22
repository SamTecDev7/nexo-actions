import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nexo_actions.agent.visual_verification import verify_visual_effect
from nexo_actions.tool_registry import ToolSpec


class FakeSemanticClient:
    provider_name = "openai"
    supports_semantic_tools = True
    model = "gpt-5.6-sol"
    reasoning_effort = "low"
    store = True
    usage_ledger = None

    def __init__(self):
        self.responses = [
            {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "observe_desktop",
                            "arguments": {},
                        },
                    }],
                },
            },
            {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "report_visual_verification",
                            "arguments": {
                                "effect_visible": True,
                                "summary": "A confirmação de envio está visível.",
                            },
                        },
                    }],
                },
            },
            {"message": {"content": "Verificação concluída.", "tool_calls": []}},
        ]

    def chat(self, _messages, _tools, **_kwargs):
        return self.responses.pop(0)


class VisualVerificationTests(unittest.TestCase):
    def test_restricted_verifier_requires_observation_and_structured_verdict(self):
        source = FakeSemanticClient()
        observe = ToolSpec(
            "observe_desktop",
            "Observe",
            {
                "type": "object",
                "properties": {
                    "scope": {"type": "string"},
                    "max_width": {"type": "integer"},
                    "max_height": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            lambda **_kwargs: {
                "ok": True,
                "observation_id": "after-1",
                "image_base64": "aGVsbG8=",
            },
            category="desktop",
            log_policy="none",
        )

        with patch(
            "nexo_actions.agent.visual_verification.OpenAIResponsesClient",
            return_value=source,
        ):
            result = verify_visual_effect(
                SimpleNamespace(client=source),
                action={
                    "action": "click",
                    "confirmation_reason": "enviar o formulário",
                },
                observe_spec=observe,
            )

        self.assertTrue(result["observation_completed"])
        self.assertTrue(result["effect_visible"])
        self.assertTrue(result["verified"])
        self.assertIn("confirmação", result["summary"])

    def test_local_provider_never_claims_visual_verification(self):
        result = verify_visual_effect(
            SimpleNamespace(client=SimpleNamespace(provider_name="ollama")),
            action={"action": "click"},
            observe_spec=SimpleNamespace(),
        )
        self.assertFalse(result["verified"])
        self.assertFalse(result["observation_completed"])


if __name__ == "__main__":
    unittest.main()
