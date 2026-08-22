#!/usr/bin/env python3
"""Live, self-cleaning agent test for the requested Notepad workflow."""

from __future__ import annotations

import json
import time
import uuid

from nexo_actions.agent import AgentOrchestrator
from nexo_actions.config import known_directories, windows_to_wsl
from nexo_actions.desktop_tools import list_windows, window_action
from nexo_actions.tools import TOOL_REGISTRY


def main() -> int:
    filename = f"lembrete-nexo-{uuid.uuid4().hex[:8]}.txt"
    content = "reunião amanhã às nove"
    target = windows_to_wsl(known_directories()["downloads"]) / filename
    report = {}
    failure = None
    try:
        agent = AgentOrchestrator(TOOL_REGISTRY, max_steps=6, timeout_seconds=60)
        report["agent"] = agent.run(
            f"Abra o Bloco de Notas, escreva '{content}' e salve em Downloads como {filename}."
        )
        time.sleep(2)
        report["file_exists"] = target.exists()
        report["content"] = target.read_text(encoding="utf-8") if target.exists() else None
        matches = [
            item for item in list_windows()["windows"]
            if filename.casefold() in item["title"].casefold()
        ]
        report["notepad_window"] = matches[0]["title"] if matches else None
        if matches:
            window_action("close", filename)
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            failure = "O workflow não produziu o arquivo com o conteúdo esperado"
        elif "create_text_file" not in [step["name"] for step in report["agent"]["steps"]]:
            failure = "O agente não usou o caminho robusto create_text_file"
    finally:
        if target.exists():
            target.unlink()
    report["cleaned_up"] = not target.exists()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failure:
        raise RuntimeError(failure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
