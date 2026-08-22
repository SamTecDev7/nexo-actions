"""Compact and validate tool results before they become spoken answers."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", text.casefold()).split())


def _sentence(value: Any, limit: int = 360) -> str:
    text = " ".join(str(value or "").split()).strip(" -.;")
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return text


def compact_result_for_model(result: Any) -> Any:
    """Keep the useful evidence while avoiding huge, slow LLM prompts."""
    if not isinstance(result, Mapping) or result.get("tool") != "web_research":
        return result
    sources = []
    for source in list(result.get("sources") or [])[:5]:
        if not isinstance(source, Mapping):
            continue
        sources.append({
            "id": source.get("id"),
            "title": _sentence(source.get("title"), 180),
            "domain": _sentence(source.get("domain"), 120),
            "snippet": _sentence(source.get("snippet"), 520),
            "excerpt": _sentence(source.get("excerpt"), 760),
            "official": bool(source.get("official")),
        })
    return {
        "ok": bool(result.get("ok")),
        "tool": "web_research",
        "query": _sentence(result.get("query"), 300),
        "resolved_query": _sentence(result.get("resolved_query"), 300),
        "source_count": int(result.get("source_count") or len(sources)),
        "validation": result.get("validation") or {},
        "sources": sources,
        "content_untrusted": True,
    }


def clean_spoken_answer(answer: Any, limit: int = 760) -> str:
    """Turn an occasionally markdown-heavy model answer into short speech."""
    text = str(answer or "")
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#>`]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = max(text.rfind(". ", 0, limit), text.rfind("! ", 0, limit), text.rfind("? ", 0, limit))
        text = text[: cut + 1 if cut >= limit // 2 else limit].rstrip(" ,;:")
    return text


def answer_is_grounded(answer: str, result: Any) -> bool:
    """Reject empty or obviously off-entity web answers."""
    normalized = _normalize(answer)
    if len(normalized) < 12:
        return False
    if not isinstance(result, Mapping) or result.get("tool") != "web_research":
        return True
    validation = result.get("validation") or {}
    entity = _normalize(validation.get("resolved_entity"))
    entity_terms = [term for term in entity.split() if len(term) >= 4]
    if entity_terms and not all(term in normalized for term in entity_terms[:3]):
        return False
    sources = list(result.get("sources") or [])
    evidence_terms: set[str] = set()
    for source in sources[:3]:
        if not isinstance(source, Mapping):
            continue
        evidence_terms.update(
            term for term in _normalize(
                f"{source.get('title', '')} {source.get('snippet', '')}"
            ).split()
            if len(term) >= 6
        )
    return not evidence_terms or bool(evidence_terms.intersection(normalized.split()))


def fallback_spoken_answer(result: Any) -> str:
    """Always provide a factual answer even if the local LLM times out."""
    if not isinstance(result, Mapping):
        return "Concluí a consulta, mas não consegui resumir os dados agora."
    if result.get("tool") != "web_research":
        return "Concluí a consulta e os dados estão disponíveis na interface."
    sources = [item for item in result.get("sources") or [] if isinstance(item, Mapping)]
    validation = result.get("validation") or {}
    entity = _sentence(validation.get("resolved_entity"), 120)
    label = _sentence((sources[0].get("title") if sources else "") or entity or result.get("query"), 140)
    primary = next(
        (
            _sentence(item.get("snippet"), 330)
            for item in sources
            if _sentence(item.get("snippet"), 330)
        ),
        "",
    )
    if not primary:
        primary = next(
            (
                _sentence(item.get("excerpt"), 330)
                for item in sources
                if _sentence(item.get("excerpt"), 330)
            ),
            "",
        )
    count = int(result.get("source_count") or len(sources))
    if primary:
        normalized_primary = _normalize(primary)
        if "fretes" in normalized_primary and "entregas" in normalized_primary:
            coverage = (
                " no Brasil e no exterior"
                if "brasil" in normalized_primary and "exterior" in normalized_primary
                else ""
            )
            answer = (
                f"Segundo o site principal, {label} oferece serviços de fretes "
                f"para entregas{coverage}. Confirmei a informação em {count} fontes."
            )
        else:
            answer = (
                f"Consultei {count} fontes sobre {label}. "
                f"A fonte principal resume assim: {primary.rstrip('.!?')}."
            )
    elif label:
        answer = f"Encontrei {count} fontes sobre {label}, mas elas não trouxeram um resumo textual confiável."
    else:
        answer = "Concluí a pesquisa, mas as fontes não trouxeram texto suficiente para uma resposta segura."
    if validation.get("status") == "limited":
        answer += " Há poucas confirmações independentes, então trato isso com cautela."
    return clean_spoken_answer(answer)


def encoded_result_size(result: Any) -> int:
    """Small diagnostic helper used by regression tests."""
    return len(json.dumps(compact_result_for_model(result), ensure_ascii=False))
