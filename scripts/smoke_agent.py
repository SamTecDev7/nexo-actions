#!/usr/bin/env python3
"""Read-only live smoke test for the Gemma multi-tool loop."""

from __future__ import annotations

import json
import time

from nexo_actions.agent import AgentOrchestrator
from nexo_actions.tools import TOOL_REGISTRY


def main() -> int:
    agent = AgentOrchestrator(TOOL_REGISTRY, max_steps=8, timeout_seconds=90)
    started = time.perf_counter()
    result = agent.run("Meu computador está lento. Descobre o que está acontecendo.")
    result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    required = {"system_status"}
    names = [step["name"] for step in result["steps"]]
    if not required.issubset(names) or names.count("list_processes") < 2:
        raise RuntimeError("O diagnóstico não coletou o conjunto mínimo de ferramentas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
