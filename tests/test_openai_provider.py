import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexo_actions.llm.base import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
)
from nexo_actions.llm.openai_provider import OpenAIResponsesClient
from nexo_actions.usage import UsageLedger


class RecordingTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def __call__(self, payload, timeout):
        self.payloads.append((payload, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def function_schema(name="get_volume"):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "L� o volume",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }


class OpenAIProviderTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tmp_path = Path(folder.name)

    def test_translates_response_function_call_and_continuation(self):
        transport = RecordingTransport([
            {
                "id": "resp_1",
                "model": "gpt-5.6-sol",
                "output": [{
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "get_volume",
                    "arguments": "{}",
                }],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 3,
                    "total_tokens": 13,
                },
            },
            {
                "id": "resp_2",
                "model": "gpt-5.6-sol",
                "output_text": "O volume est� em 30 por cento.",
                "output": [],
                "usage": {
                    "input_tokens": 15,
                    "output_tokens": 8,
                    "total_tokens": 23,
                },
            },
        ])
        client = OpenAIResponsesClient(
            transport=transport,
            usage_ledger=UsageLedger(self.tmp_path / "usage.jsonl"),
            max_retries=0,
        )
        first = client.chat(
            [
                {"role": "system", "content": "Use tools"},
                {"role": "user", "content": "qual o volume?"},
            ],
            [function_schema()],
            timeout=5,
        )
        call = first["message"]["tool_calls"][0]
        self.assertEqual(call["id"], "call_1")
        self.assertIs(transport.payloads[0][0]["parallel_tool_calls"], False)
        self.assertEqual(
            transport.payloads[0][0]["text"],
            {"verbosity": "low"},
        )

        second = client.chat(
            [
                {"role": "system", "content": "Use tools"},
                {"role": "user", "content": "qual o volume?"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [call],
                },
                {
                    "role": "tool",
                    "tool_name": "get_volume",
                    "content": json.dumps({"ok": True, "volume": 30}),
                },
            ],
            [function_schema()],
            timeout=5,
        )
        self.assertEqual(
            second["message"]["content"],
            "O volume est� em 30 por cento.",
        )
        continuation = transport.payloads[1][0]
        self.assertEqual(continuation["previous_response_id"], "resp_1")
        self.assertEqual(continuation["input"], [{
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"ok":true,"volume":30}',
        }])
        self.assertEqual(
            {tool["type"] for tool in continuation["tools"]},
            {"function", "web_search"},
        )

    def test_screen_capture_becomes_image_input_without_remaining_in_output(self):
        transport = RecordingTransport([{
            "id": "resp",
            "output_text": "Vejo a tela.",
            "output": [],
            "usage": {},
        }])
        client = OpenAIResponsesClient(
            transport=transport,
            usage_ledger=UsageLedger(self.tmp_path / "usage"),
            max_retries=0,
        )
        client.chat(
            [
                {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "call_x",
                        "function": {
                            "name": "observe_desktop",
                            "arguments": "{}",
                        },
                    }],
                },
                {
                    "role": "tool",
                    "tool_name": "observe_desktop",
                    "content": json.dumps({
                        "ok": True,
                        "image_base64": "YWJj",
                    }),
                },
            ],
            [],
            timeout=5,
        )
        payload = transport.payloads[0][0]
        image_input = next(
            content
            for item in payload["input"]
            if item.get("role") == "user" and isinstance(item.get("content"), list)
            for content in item["content"]
            if content.get("type") == "input_image"
        )
        self.assertEqual(image_input["image_url"], "data:image/png;base64,YWJj")
        self.assertEqual(image_input["detail"], "high")
        function_output = next(
            item
            for item in payload["input"]
            if item.get("type") == "function_call_output"
        )
        serialized_output = function_output["output"]
        self.assertNotIn("image_base64", serialized_output)
        self.assertNotIn("YWJj", function_output["output"])

    def test_screen_capture_in_continuation_uses_high_detail_and_redacts_output(self):
        transport = RecordingTransport([
            {
                "id": "resp_observe",
                "output": [{
                    "type": "function_call",
                    "call_id": "call_observe",
                    "name": "observe_desktop",
                    "arguments": "{}",
                }],
                "usage": {},
            },
            {
                "id": "resp_answer",
                "output_text": "Consigo ler a tela.",
                "output": [],
                "usage": {},
            },
        ])
        client = OpenAIResponsesClient(
            transport=transport,
            usage_ledger=UsageLedger(self.tmp_path / "usage-continuation"),
            max_retries=0,
        )
        first = client.chat(
            [{"role": "user", "content": "Leia a tela"}],
            [function_schema("observe_desktop")],
            timeout=5,
        )
        call = first["message"]["tool_calls"][0]

        client.chat(
            [
                {"role": "user", "content": "Leia a tela"},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {
                    "role": "tool",
                    "tool_name": "observe_desktop",
                    "content": json.dumps({
                        "ok": True,
                        "observation": {
                            "observation_id": "obs-1",
                            "image_base64": "ZGVm",
                        },
                    }),
                },
            ],
            [function_schema("observe_desktop")],
            timeout=5,
        )

        continuation = transport.payloads[1][0]
        self.assertEqual(continuation["previous_response_id"], "resp_observe")
        function_output = next(
            item
            for item in continuation["input"]
            if item.get("type") == "function_call_output"
        )
        self.assertNotIn("image_base64", function_output["output"])
        self.assertNotIn("ZGVm", function_output["output"])
        image_input = next(
            content
            for item in continuation["input"]
            if item.get("role") == "user" and isinstance(item.get("content"), list)
            for content in item["content"]
            if content.get("type") == "input_image"
        )
        self.assertEqual(image_input["image_url"], "data:image/png;base64,ZGVm")
        self.assertEqual(image_input["detail"], "high")

    def test_authentication_error_is_not_retried(self):
        transport = RecordingTransport([
            ProviderAuthenticationError("invalid"),
        ])
        client = OpenAIResponsesClient(
            transport=transport,
            usage_ledger=UsageLedger(self.tmp_path / "usage"),
            max_retries=2,
        )

        with self.assertRaises(ProviderAuthenticationError):
            client.chat(
                [{"role": "user", "content": "oi"}],
                [],
                timeout=5,
            )
        self.assertEqual(len(transport.payloads), 1)

    def test_rate_limit_retries_once(self):
        transport = RecordingTransport([
            ProviderRateLimitError("rate"),
            {
                "id": "resp",
                "output_text": "ok",
                "output": [],
                "usage": {},
            },
        ])
        client = OpenAIResponsesClient(
            transport=transport,
            usage_ledger=UsageLedger(self.tmp_path / "usage"),
            max_retries=1,
        )

        with patch(
            "nexo_actions.llm.openai_provider.time.sleep",
            return_value=None,
        ):
            response = client.chat(
                [{"role": "user", "content": "oi"}],
                [],
                timeout=5,
            )

        self.assertEqual(response["message"]["content"], "ok")
        self.assertEqual(len(transport.payloads), 2)

    def test_incomplete_or_empty_response_is_never_reported_as_success(self):
        incomplete = OpenAIResponsesClient(
            transport=RecordingTransport([{
                "id": "resp-incomplete",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [],
                "usage": {},
            }]),
            usage_ledger=UsageLedger(self.tmp_path / "usage-incomplete"),
            max_retries=0,
        )
        with self.assertRaises(ProviderError):
            incomplete.chat([{"role": "user", "content": "faça algo"}], [], timeout=5)

        empty = OpenAIResponsesClient(
            transport=RecordingTransport([{
                "id": "resp-empty",
                "status": "completed",
                "output": [],
                "usage": {},
            }]),
            usage_ledger=UsageLedger(self.tmp_path / "usage-empty"),
            max_retries=0,
        )
        with self.assertRaises(ProviderError):
            empty.chat([{"role": "user", "content": "faça algo"}], [], timeout=5)

    def test_usage_ledger_records_metadata_only(self):
        path = self.tmp_path / "usage.jsonl"
        ledger = UsageLedger(path)
        ledger.record(
            provider="openai",
            model="gpt-test",
            usage={
                "input_tokens": 2,
                "output_tokens": 3,
                "total_tokens": 5,
            },
            latency_ms=10,
        )

        text = path.read_text(encoding="utf-8")
        self.assertNotIn("prompt", text)
        self.assertNotIn("api_key", text)
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot["session"]["requests"], 1)
        self.assertEqual(snapshot["session"]["total_tokens"], 5)
        self.assertIsNone(snapshot["estimated_cost"])


if __name__ == "__main__":
    unittest.main()
