import unittest
from unittest.mock import patch

from nexo_actions.skill import NexoActionsSkill
from nexo_actions.web_research import _public_url, _relevance, _research_plan, web_research


class WebSecurityTests(unittest.TestCase):
    def test_public_fetch_rejects_private_dns_results(self):
        with patch("nexo_actions.web_research.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 80)),
        ]):
            with self.assertRaises(ValueError):
                _public_url("http://example.com/secret")

    def test_research_returns_ranked_source_evidence(self):
        results = [{"url": "https://example.com/", "title": "Example", "snippet": "Public snippet"}]
        source = {
            "rank": 1, "title": "Example Inc", "url": "https://example.com/", "domain": "example.com",
            "snippet": "Public snippet", "excerpt": "Example makes safe test products.", "published_at": None,
        }
        public_dns = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch("nexo_actions.web_research.socket.getaddrinfo", return_value=public_dns), \
             patch("nexo_actions.web_research._search", return_value=results), \
             patch("nexo_actions.web_research._official_candidates", return_value=[]), \
             patch("nexo_actions.web_research._fetch_source", return_value=source):
            result = web_research("Quem é a Example Inc?", max_results=1)
        self.assertEqual(result["source_count"], 1)
        self.assertTrue(result["sources"][0]["content_untrusted"])
        self.assertIn("Example makes", result["answer_context"])
        self.assertEqual(result["validation"]["mode"], "sequential_entity_relevance")

    def test_stt_corruption_resolves_configured_company_and_rejects_football(self):
        with patch("nexo_actions.web_research.load_config", return_value={
            "site_aliases": {"clube envios": "https://clubeenvios.com.br"}
        }):
            plan = _research_plan(
                "Isso, pode buscar agora o que é clube em views?",
                "O Clube Envios é uma plataforma de frete em Goiânia",
            )
        self.assertEqual(plan["alias"]["name"], "clube envios")
        self.assertIn('"clube envios"', plan["search_query"].casefold())
        company_score, _ = _relevance({
            "url": "https://clubeenvios.com.br",
            "title": "Clube Envios | Frete para e-commerce",
            "snippet": "Plataforma de logística de Goiânia",
            "official": True,
        }, plan)
        football_score, _ = _relevance({
            "url": "https://sports.example/noticia",
            "title": "Clube anuncia partidas do campeonato de futebol",
            "snippet": "Jogos no estádio neste domingo",
        }, plan)
        self.assertGreater(company_score, 20)
        self.assertLess(football_score, 0)


class WebDecisionTests(unittest.TestCase):
    def setUp(self):
        class DecisionHarness:
            _normalize = staticmethod(NexoActionsSkill._normalize)
            _should_research_web = NexoActionsSkill._should_research_web
            _web_research_call = NexoActionsSkill._web_research_call
            _fast_route_tool = NexoActionsSkill._fast_route_tool

        self.skill = DecisionHarness()
        self.skill.nexo_config = {"site_aliases": {"clube envios": "https://clubeenvios.com.br"}}
        self.skill.conversation = None

    def test_company_question_and_current_facts_trigger_research(self):
        self.assertTrue(self.skill._should_research_web("Quem é a empresa Clube Envios?"))
        self.assertTrue(self.skill._should_research_web("Qual é a notícia mais recente sobre a empresa?"))
        self.assertFalse(self.skill._should_research_web("Abaixa o volume para vinte"))
        self.assertFalse(self.skill._should_research_web(
            "Centralize o formulário; atualmente ele não está no meio da tela"
        ))

    def test_admin_and_research_have_deterministic_routes(self):
        admin = self.skill._fast_route_tool("abre as configurações ADM do Nexo")
        research = self.skill._fast_route_tool("pesquise quem é a empresa Clube Envios")
        browser = self.skill._fast_route_tool("pesquise no Google restaurantes próximos")
        self.assertEqual(admin["name"], "open_nexo_admin")
        self.assertEqual(research["name"], "web_research")
        self.assertEqual(browser["name"], "web_search")

    def test_action_word_agora_does_not_force_news_freshness(self):
        call = self.skill._web_research_call("Pode buscar agora o Clube Envios")
        self.assertEqual(call["arguments"]["freshness"], "any")
        current = self.skill._web_research_call("Qual é a cotação agora?")
        self.assertEqual(current["arguments"]["freshness"], "day")


if __name__ == "__main__":
    unittest.main()
