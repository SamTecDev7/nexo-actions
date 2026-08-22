import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from ovos_workshop.skills.converse import ConversationalSkill
from ovos_workshop.skills.fallback import FallbackSkill

from nexo_actions.agent import ConversationManager
from nexo_actions.skill import NexoActionsSkill


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ConversationManagerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.manager = ConversationManager(
            ttl_seconds=30,
            max_messages=8,
            max_tokens=400,
            summary_chars=400,
            clock=self.clock,
        )

    def test_session_lifecycle_and_ttl(self):
        session_id = self.manager.start()
        self.assertTrue(session_id)
        self.assertTrue(self.manager.active)
        self.clock.advance(29)
        self.assertTrue(self.manager.active)
        self.clock.advance(1)
        self.assertFalse(self.manager.active)
        self.assertTrue(self.manager.expired)
        self.manager.end("timeout")
        self.assertFalse(self.manager.expired)
        self.assertEqual(self.manager.snapshot()["ended_reason"], "timeout")

    def test_activity_refreshes_ttl(self):
        self.manager.start()
        self.clock.advance(20)
        self.manager.add_user("ainda estou aqui")
        self.clock.advance(20)
        self.assertTrue(self.manager.active)

    def test_tool_calls_keep_pronoun_references(self):
        self.manager.start()
        self.manager.add_user("abre o Chrome")
        self.manager.add_tool_call("open_app", {"app_name": "Chrome"})
        self.manager.add_tool_result(
            "open_app",
            {"ok": True, "requested": "Chrome", "matched": "Google Chrome"},
        )
        refs = self.manager.references
        self.assertEqual(refs["last_app"], "Google Chrome")
        self.assertEqual(refs["last_tool"], "open_app")
        self.assertIn("last_app=Google Chrome", self.manager.context_text())

    def test_large_history_is_compacted(self):
        self.manager.start()
        for index in range(20):
            self.manager.add_user(f"pergunta {index} " + "x" * 80)
            self.manager.add_assistant(f"resposta {index} " + "y" * 80)
        snapshot = self.manager.snapshot()
        self.assertLessEqual(len(snapshot["messages"]), 8)
        self.assertTrue(snapshot["summary"])
        self.assertLessEqual(len(snapshot["summary"]), 400)
        self.assertLessEqual(snapshot["approx_tokens"], 400)

    def test_single_oversized_turn_respects_token_budget(self):
        self.manager.start()
        self.manager.add_user("pedido " + "muito longo " * 1000)
        self.manager.add_assistant("resposta " + "detalhada " * 1000)
        self.assertLessEqual(
            self.manager.snapshot()["approx_tokens"],
            self.manager.max_tokens,
        )

    def test_tool_results_are_bounded_and_secrets_redacted(self):
        self.manager.start()
        self.manager.add_tool_result(
            "email_search",
            {
                "ok": True,
                "token": "never-log-this",
                "items": ["x" * 1000 for _ in range(20)],
            },
        )
        text = self.manager.model_messages()[-1]["content"]
        self.assertNotIn("never-log-this", text)
        self.assertLess(len(text), 1000)

    def test_model_messages_include_summary_and_references(self):
        self.manager.start()
        self.manager.add_tool_call("process_status", {"query": "Chrome"})
        messages = self.manager.model_messages()
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("last_process=Chrome", messages[0]["content"])

    def test_recent_file_reference_survives_a_separate_wakeword_command(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "formulario.html"
            path.write_text("<form></form>", encoding="utf-8")
            self.manager.remember_tool_call(
                "create_text_file",
                {"directory": directory, "filename": "formulario.html"},
            )
            self.manager.remember_tool_result(
                "create_text_file",
                {"ok": True, "path": str(path)},
            )

            self.assertFalse(self.manager.active)
            context = self.manager.reference_context()
            self.assertIn("formulario.html", context)
            self.assertIn("last_path", context)

            self.clock.advance(30)
            self.assertEqual(self.manager.reference_context(), "")

    def test_deleted_recent_file_is_pruned_before_the_next_command(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "temporario.css"
            path.write_text("body {}", encoding="utf-8")
            self.manager.remember_tool_result(
                "read_text_file",
                {"ok": True, "path": str(path)},
            )
            path.unlink()
            self.manager.remember_user("Deixe a cor de fundo mais escura")

            messages = self.manager.recent_model_messages()
            self.assertNotIn("last_path", self.manager.references)
            self.assertNotIn("recent_files", self.manager.references)
            self.assertIn("não existe mais", messages[0]["content"])
            self.assertNotIn(str(path), messages[0]["content"])

    def test_one_shot_turns_preserve_goal_between_wakeword_commands(self):
        self.manager.remember_user(
            "Quero que você centralize o formulário no meio da tela"
        )
        self.manager.remember_tool_result(
            "read_text_file",
            {
                "ok": True,
                "path": "C:\\Users\\Administrador\\Downloads\\index.html",
            },
        )
        self.manager.remember_assistant("Li o formulário em Downloads.")
        self.manager.remember_user("Pode editar o arquivo.")

        messages = self.manager.recent_model_messages()
        contents = [item["content"] for item in messages]
        self.assertTrue(any("centralize o formulário" in item for item in contents))
        self.assertEqual(contents[-1], "Pode editar o arquivo.")
        self.assertIn("last_path", contents[0])
        self.assertFalse(self.manager.active)

        self.clock.advance(30)
        self.assertEqual(self.manager.recent_model_messages(), [])

    def test_duplicate_one_shot_command_is_not_added_twice(self):
        self.manager.remember_user("Edite o arquivo")
        self.manager.remember_user("Edite o arquivo")
        self.assertEqual(
            [item["content"] for item in self.manager.recent_model_messages()],
            ["Edite o arquivo"],
        )


class ConversationSkillContractTests(unittest.TestCase):
    def test_live_local_telemetry_does_not_trigger_web_research(self):
        skill = SimpleNamespace(
            _normalize=NexoActionsSkill._normalize,
            nexo_config={"site_aliases": {}},
        )
        decide = NexoActionsSkill._should_research_web
        self.assertFalse(decide(skill, "Qual é o uso atual da CPU?"))
        self.assertFalse(decide(skill, "Como está a temperatura da GPU agora?"))
        self.assertTrue(decide(skill, "Pesquise na internet notícias sobre novas CPUs"))

    def test_local_css_state_does_not_trigger_web_research(self):
        skill = SimpleNamespace(
            _normalize=NexoActionsSkill._normalize,
            nexo_config={"site_aliases": {}},
        )
        decide = NexoActionsSkill._should_research_web
        self.assertFalse(decide(
            skill,
            "Centralize esse formulário no meio da tela. Atualmente ele não está.",
        ))
        self.assertFalse(decide(skill, "Melhore o CSS atual do arquivo em Downloads"))
        self.assertTrue(decide(
            skill,
            "Pesquise na internet fontes atuais sobre centralização com CSS",
        ))

    def test_natural_visual_change_is_a_local_action(self):
        skill = SimpleNamespace(
            _normalize=NexoActionsSkill._normalize,
            nexo_config={"site_aliases": {}},
            conversation=SimpleNamespace(
                reference_context=lambda: "last_path=C:\\Downloads\\style.css"
            ),
        )
        utterance = "Deixe a cor de fundo mais escura"
        self.assertTrue(NexoActionsSkill._looks_like_action(skill, utterance))
        self.assertTrue(NexoActionsSkill._looks_like_multi_action(skill, utterance))
        self.assertFalse(NexoActionsSkill._should_research_web(skill, utterance))

    def test_nexo_wakeword_reserves_the_next_turn(self):
        activated = []
        skill = SimpleNamespace(
            _normalize=NexoActionsSkill._normalize,
            _wakeword_capture_until=0.0,
            activate=lambda **kwargs: activated.append(kwargs),
        )
        original = __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic
        try:
            __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic = lambda: 100.0
            NexoActionsSkill._handle_nexo_wakeword(
                skill,
                SimpleNamespace(data={"utterance": "nexo"}),
            )
        finally:
            __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic = original
        self.assertEqual(skill._wakeword_capture_until, 145.0)
        self.assertEqual(activated, [{"duration_minutes": 1}])

    def test_stt_failure_clears_wake_capture_and_never_leaves_thinking(self):
        spoken = []
        states = []
        skill = SimpleNamespace(
            _wakeword_capture_until=float("inf"),
            conversation=SimpleNamespace(active=False),
            speak=lambda text, **kwargs: spoken.append((text, kwargs)),
            _emit_ui_state=lambda state, **kwargs: states.append((state, kwargs)),
        )

        NexoActionsSkill._handle_stt_unknown(skill, None)

        self.assertEqual(skill._wakeword_capture_until, 0.0)
        self.assertIn("Pode repetir", spoken[0][0])
        self.assertEqual(states, [])

    def test_skill_combines_fallback_and_native_converse_contracts(self):
        self.assertTrue(issubclass(NexoActionsSkill, FallbackSkill))
        self.assertTrue(issubclass(NexoActionsSkill, ConversationalSkill))
        self.assertFalse(bool(getattr(NexoActionsSkill, "__abstractmethods__", set())))

    def test_exit_phrases_are_normalized(self):
        skill = SimpleNamespace(_normalize=NexoActionsSkill._normalize)
        check = NexoActionsSkill._is_conversation_exit
        for utterance in (
            "Obrigado, é só isso.",
            "Pode parar por agora",
            "Tchau, tchau",
            "Nexo, até a próxima",
            "É isso, senhor.",
            "Pode encerrar",
            "Saia do modo conversa contínua",
        ):
            self.assertTrue(check(skill, utterance), utterance)
        self.assertFalse(check(skill, "O que significa tchau?"))
        self.assertFalse(check(skill, "O que é isso?"))
        self.assertFalse(check(skill, "Pode abrir o Chrome"))

    def test_start_phrases_are_normalized(self):
        skill = SimpleNamespace(_normalize=NexoActionsSkill._normalize)
        check = NexoActionsSkill._is_conversation_start
        self.assertTrue(check(skill, "Nexo, vamos conversar."))
        self.assertTrue(check(skill, "Quero conversar com você"))
        self.assertFalse(check(skill, "Abra o Chrome"))

    def test_echo_filter_requires_recent_tts_and_meaningful_overlap(self):
        skill = SimpleNamespace(
            _normalize=NexoActionsSkill._normalize,
            _tts_active=True,
            _last_tts_end=0,
            _recent_spoken=[(100, "sua gpu esta usando vinte por cento")],
        )
        check = NexoActionsSkill._is_likely_tts_echo
        original = __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic
        try:
            __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic = lambda: 101
            self.assertTrue(check(skill, "Sua GPU está usando vinte por cento"))
            self.assertFalse(check(skill, "fecha o Chrome"))
        finally:
            __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic = original

    def test_record_boundary_does_not_blindly_stop_tts(self):
        emitted = []
        skill = SimpleNamespace(
            _tts_active=True,
            _record_during_tts=0.0,
            bus=SimpleNamespace(emit=emitted.append),
        )
        original = __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic
        try:
            __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic = lambda: 101
            NexoActionsSkill._handle_record_end(skill, SimpleNamespace())
        finally:
            __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic = original
        self.assertEqual(skill._record_during_tts, 101)
        self.assertEqual(emitted, [])

    def test_confirmed_wakeword_barge_in_stops_only_speech(self):
        emitted = []
        skill = SimpleNamespace(
            _barge_in_enabled=True,
            _barge_in_requires_wakeword=True,
            _normalize=NexoActionsSkill._normalize,
            _tts_active=True,
            _tts_deadline=1e30,
            _last_tts_end=0.0,
            conversation=SimpleNamespace(active=True),
            skill_id="nexo-actions.samtec",
            bus=SimpleNamespace(emit=emitted.append),
        )
        self.assertTrue(NexoActionsSkill._confirm_human_barge_in(skill, "Nexo, espera"))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].msg_type, "mycroft.audio.speech.stop")
        self.assertEqual(emitted[0].data["reason"], "confirmed_human_barge_in")
        self.assertFalse(skill._tts_active)

    def test_background_speech_cannot_interrupt_tts(self):
        emitted = []
        skill = SimpleNamespace(
            _barge_in_enabled=True,
            _barge_in_requires_wakeword=True,
            _normalize=NexoActionsSkill._normalize,
            _tts_active=True,
            _tts_deadline=1e30,
            _last_tts_end=0.0,
            conversation=SimpleNamespace(active=True),
            skill_id="nexo-actions.samtec",
            bus=SimpleNamespace(emit=emitted.append),
        )
        self.assertFalse(NexoActionsSkill._confirm_human_barge_in(skill, "Mas o que é termo?"))
        self.assertEqual(emitted, [])
        self.assertTrue(skill._tts_active)

    def test_wakeword_must_be_at_the_start_for_barge_in(self):
        skill = SimpleNamespace(_normalize=NexoActionsSkill._normalize)
        check = NexoActionsSkill._starts_with_wakeword
        self.assertTrue(check(skill, "Nexo, mas o que é termo?"))
        self.assertTrue(check(skill, "Ô Nexo, pode parar"))
        self.assertTrue(check(skill, "Necesso, espera"))
        self.assertFalse(check(skill, "Mas Nexo foi citado depois"))

    def test_listener_mode_is_set_before_state_reset(self):
        emitted = []
        skill = SimpleNamespace(
            skill_id="nexo-actions.samtec",
            bus=SimpleNamespace(emit=emitted.append),
        )
        NexoActionsSkill._set_listener_mode(skill, "wakeword")
        self.assertEqual(len(emitted), 2)
        self.assertEqual(emitted[0].data, {"mode": "wakeword"})
        self.assertEqual(emitted[1].data, {"state": "wakeword"})

        emitted.clear()
        NexoActionsSkill._set_listener_mode(skill, "continuous")
        self.assertEqual(emitted[0].data, {"mode": "continuous"})
        self.assertEqual(emitted[1].data, {"state": "continuous"})

    def test_stale_tts_state_expires_without_native_end_event(self):
        skill = SimpleNamespace(
            _tts_active=True,
            _tts_deadline=100.0,
            _last_tts_end=0.0,
        )
        original = __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic
        try:
            __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic = lambda: 101
            self.assertFalse(NexoActionsSkill._tts_output_active(skill))
        finally:
            __import__("nexo_actions.skill", fromlist=["time"]).time.monotonic = original
        self.assertFalse(skill._tts_active)
        self.assertEqual(skill._last_tts_end, 100.0)


if __name__ == "__main__":
    unittest.main()
