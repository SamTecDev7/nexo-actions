import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nexo_actions.response_grounding import (
    answer_is_grounded,
    clean_spoken_answer,
    compact_result_for_model,
    encoded_result_size,
    fallback_spoken_answer,
)
from nexo_actions.skill import NexoActionsSkill


def research_result():
    return {
        "ok": True,
        "tool": "web_research",
        "query": "quem é a Clube Envios em Goiânia",
        "resolved_query": '"clube envios" goiania',
        "source_count": 5,
        "validation": {
            "status": "confirmed",
            "resolved_entity": "clube envios",
        },
        "answer_context": "x" * 80_000,
        "sources": [{
            "title": "Clube Envios",
            "domain": "clubeenvios.com.br",
            "snippet": "Revolucione suas entregas com fretes mais baratos em todo o Brasil e no exterior!",
            "excerpt": "Localização em Goiânia. " + "y" * 20_000,
            "official": True,
        }],
    }


class ResponseGroundingTests(unittest.TestCase):
    def test_web_payload_is_compacted_before_the_llm_call(self):
        compact = compact_result_for_model(research_result())
        self.assertNotIn("answer_context", compact)
        self.assertLess(encoded_result_size(research_result()), 4_000)

    def test_off_topic_answer_is_rejected(self):
        self.assertFalse(answer_is_grounded(
            "Os parênteses são usados para informações acessórias.",
            research_result(),
        ))
        self.assertTrue(answer_is_grounded(
            "A Clube Envios é uma plataforma de fretes e entregas.",
            research_result(),
        ))

    def test_fallback_always_returns_source_grounded_speech(self):
        answer = fallback_spoken_answer(research_result())
        self.assertIn("Clube Envios", answer)
        self.assertIn("fretes", answer)
        self.assertNotIn("http", answer)

    def test_markdown_and_long_answers_are_cleaned_for_voice(self):
        answer = clean_spoken_answer("## **Título** " + "texto " * 500)
        self.assertNotIn("*", answer)
        self.assertLessEqual(len(answer), 760)

    def test_completed_research_is_spoken_even_when_renderer_times_out(self):
        spoken = []
        skill = SimpleNamespace(
            conversation=SimpleNamespace(
                active=False,
                reference_context=lambda: "",
            ),
            settings={},
            _default_model="gemma-test",
            _ollama_url="http://127.0.0.1:11434/api/chat",
            speak=lambda text, **kwargs: spoken.append((text, kwargs)),
        )

        with patch(
            "nexo_actions.skill.urllib.request.urlopen",
            side_effect=TimeoutError("renderer timed out"),
        ):
            NexoActionsSkill._speak_system_result(
                skill,
                "Quem é a Clube Envios em Goiânia?",
                research_result(),
            )

        self.assertEqual(len(spoken), 1)
        self.assertIn("Clube Envios", spoken[0][0])
        self.assertIn("fretes", spoken[0][0])
        self.assertEqual(len(spoken[0][1]["meta"]["nexo_sources"]), 1)


if __name__ == "__main__":
    unittest.main()
