#!/usr/bin/env python3
"""Live multimodal smoke test on Nexo's own loopback UI."""

from __future__ import annotations

import json
import time

from nexo_actions.agent.orchestrator import AgentOrchestrator
from nexo_actions.browser_tools import open_url
from nexo_actions.llm.openai_provider import OpenAIResponsesClient
from nexo_actions.tool_registry import ToolRegistry
from nexo_actions.tools import TOOL_REGISTRY


def main() -> int:
    open_url("http://127.0.0.1:8787", "chrome")
    time.sleep(2.0)
    registry = ToolRegistry([TOOL_REGISTRY.get("observe_desktop")])
    client = OpenAIResponsesClient(
        model="gpt-5.6-sol",
        reasoning_effort="low",
        web_search=False,
        max_output_tokens=1400,
        max_retries=1,
    )
    agent = AgentOrchestrator(
        registry,
        client=client,
        max_steps=2,
        timeout_seconds=70,
    )
    result = agent.run(
        "Observe a janela ativa exatamente uma vez e descreva em uma frase o que está visível."
    )
    steps = [str(item.get("name") or "") for item in result.get("steps") or []]
    answer = str(result.get("answer") or "").strip()
    receipt = (result.get("tool_results") or [{}])[0].get("result") or {}
    image_is_private = receipt.get("image_base64") == "<redacted>"
    ok = (
        bool(result.get("complete"))
        and result.get("verified") is True
        and steps == ["observe_desktop"]
        and bool(answer)
        and image_is_private
    )
    print(json.dumps({
        "ok": ok,
        "steps": steps,
        "answer_preview": answer[:400],
        "image_redacted_from_receipt": image_is_private,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
