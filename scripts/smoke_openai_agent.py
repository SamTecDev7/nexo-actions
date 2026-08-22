#!/usr/bin/env python3
"""Live, side-effect-free smoke test for Responses API + function calling."""

from __future__ import annotations

import json
from datetime import datetime

from nexo_actions.agent.orchestrator import AgentOrchestrator
from nexo_actions.llm.openai_provider import OpenAIResponsesClient
from nexo_actions.tool_registry import ToolRegistry, ToolSpec


def host_clock() -> dict[str, object]:
    now = datetime.now().astimezone()
    return {
        "ok": True,
        "date": now.date().isoformat(),
        "timezone": now.tzname() or "local",
    }


def main() -> int:
    registry = ToolRegistry([
        ToolSpec(
            name="probe_host_clock",
            description="Lê a data local confiável do computador para um teste sem efeitos colaterais.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=host_clock,
            timeout=5,
            category="diagnostic",
            risk_level="read_only",
        )
    ])
    client = OpenAIResponsesClient(
        model="gpt-5.6-sol",
        reasoning_effort="low",
        web_search=False,
        max_output_tokens=1200,
        max_retries=1,
    )
    agent = AgentOrchestrator(
        registry,
        client=client,
        max_steps=2,
        timeout_seconds=60,
    )
    result = agent.run(
        "Use probe_host_clock exatamente uma vez e responda qual é a data local de hoje."
    )
    names = [str(step.get("name") or "") for step in result.get("steps") or []]
    output = {
        "ok": bool(result.get("complete")) and result.get("verified") is True,
        "provider": client.provider_name,
        "model": client.model,
        "function_call_ok": names == ["probe_host_clock"],
        "steps": names,
        "answer_present": bool(str(result.get("answer") or "").strip()),
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if output["ok"] and output["function_call_ok"] and output["answer_present"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
