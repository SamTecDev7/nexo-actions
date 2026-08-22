#!/usr/bin/env python3
"""Live web-search smoke test for entity disambiguation and source grounding."""

from __future__ import annotations

import json
from urllib.parse import urlparse

from nexo_actions.llm.openai_provider import OpenAIResponsesClient


def main() -> int:
    client = OpenAIResponsesClient(
        model="gpt-5.6-sol",
        reasoning_effort="low",
        web_search=True,
        max_output_tokens=1800,
        max_retries=1,
    )
    response = client.chat(
        [
            {
                "role": "system",
                "content": (
                    "Pesquise antes de responder. Confirme entidade, cidade e ramo. "
                    "Não confunda empresas com clubes esportivos nem notícias de envio."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Quem é a empresa Clube Envios, ligada a logística e fretes, "
                    "em Goiânia, Goiás? Resuma em duas frases com base em fontes atuais."
                ),
            },
        ],
        [],
        timeout=60,
    )
    answer = str((response.get("message") or {}).get("content") or "").strip()
    meta = response.get("nexo_meta") if isinstance(response.get("nexo_meta"), dict) else {}
    sources = meta.get("sources") if isinstance(meta.get("sources"), list) else []
    domains = sorted({
        urlparse(str(item.get("url") or "")).netloc.casefold()
        for item in sources
        if isinstance(item, dict) and item.get("url")
    })
    normalized = answer.casefold()
    ok = bool(answer) and "clube envios" in normalized and bool(sources)
    print(json.dumps({
        "ok": ok,
        "entity_present": "clube envios" in normalized,
        "source_count": len(sources),
        "source_domains": domains,
        "answer_preview": answer[:500],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
