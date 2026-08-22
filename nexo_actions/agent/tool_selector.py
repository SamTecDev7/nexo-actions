"""Domain-based tool catalog reduction before local LLM calls."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from ..tool_registry import ToolRegistry, ToolSpec


class ToolSelector:
    CATEGORY_PATTERNS = {
        "audio": r"\b(volume|som|audio|mudo|mute|silencia)\b",
        "apps": r"\b(aplicativo|programa|chrome|vscode|vs code|discord|chatgpt|notepad|bloco de notas|valorant)\b",
        "browser": r"\b(site|url|dominio|google|youtube|web|internet|navegador|pesquis|noticia|fonte|atualiz)\w*\b|\b[a-z0-9-]+\.[a-z]{2,}\b",
        "desktop": r"\b(janela|tela cheia|primeiro plano|minimiz|maximiz|restaur|desktop|area de trabalho|digita|escrev)\w*\b",
        "files": (
            r"\b(arquivo|pasta|downloads|documentos|pdf|txt|salv|copi|mov|renome|nota|"
            r"html|css|javascript|codigo|programacao|formulario|projeto|site|frontend|"
            r"edit|alter|modific|melhor|estiliz|implement|trabalh|revis|ajust|corrig|"
            r"consert|refa|centraliz|deix|torn|escure|clare|troqu|mud|cor|fundo|"
            r"background|layout|tema|visual)\w*\b"
        ),
        "storage": r"\b(armazenamento|disco|espaco|arquivos grandes|pastas grandes|ocupando)\b",
        "system": r"\b(cpu|processador|gpu|vram|ram|memoria|temperatura|computador|pc|sistema|lento|diagnost)\w*\b",
        "processes": r"\b(processo|consumindo|consome|ram|cpu|lento|pesado|fecha|encerra|mata)\w*\b",
        "network": r"\b(rede|internet|ping|latencia|download|upload|speedtest|wifi|ethernet|dns|gateway)\w*\b",
        "ai": r"\b(nexo|ollama|modelo|llm|inteligencia artificial|vram)\b",
        "email": r"\b(email|e-mail|caixa de entrada|fatura|mensagem|anexo)\w*\b",
        "power": r"\b(desliga|desligar|reinicia|reiniciar|bloqueia|suspende)\w*\b",
    }

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    @staticmethod
    def normalize(text: str) -> str:
        value = unicodedata.normalize("NFKD", str(text))
        value = "".join(char for char in value if not unicodedata.combining(char))
        return " ".join(value.casefold().split())

    def categories_for(self, utterance: str) -> set[str]:
        text = self.normalize(utterance)
        categories = {
            category
            for category, pattern in self.CATEGORY_PATTERNS.items()
            if re.search(pattern, text)
        }
        if re.search(r"\b(lento|diagnost|errad|recurso demais|o que esta acontecendo)\w*\b", text):
            categories.update({"system", "processes", "network", "ai"})
        if "bloco de notas" in text or "notepad" in text:
            categories.update({"apps", "files", "desktop"})
        if not categories:
            return set(spec.category for spec in self.registry.specs())
        return categories

    def specs_for(
        self,
        utterance: str,
        *,
        include_names: Iterable[str] = (),
    ) -> list[ToolSpec]:
        categories = self.categories_for(utterance)
        names = set(include_names)
        return [
            spec for spec in self.registry.specs()
            if spec.category in categories or spec.name in names
        ]

    def schemas_for(self, utterance: str) -> list[dict]:
        return [spec.ollama_schema() for spec in self.specs_for(utterance)]
