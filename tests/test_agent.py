import unittest
from types import SimpleNamespace

from nexo_actions import TOOL_REGISTRY
from nexo_actions.agent.orchestrator import AgentOrchestrator, SYSTEM_PROMPT
from nexo_actions.llm.base import ProviderError
from nexo_actions.llm.openai_provider import OpenAIResponsesClient
from nexo_actions.agent.tool_selector import ToolSelector
from nexo_actions.skill import NexoActionsSkill
from nexo_actions.tool_registry import ToolRegistry, ToolSpec


def schema(name):
    return {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }


class FakeClient:
    def __init__(self, messages):
        self.responses = list(messages)
        self.calls = []

    def chat(self, messages, tools, **kwargs):
        self.calls.append({"messages": messages, "tools": tools})
        return self.responses.pop(0)


class SemanticFakeClient(FakeClient):
    provider_name = "openai"
    supports_semantic_tools = True


def call(name, value=1):
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": {"value": value}}}],
        }
    }


def call_with_arguments(name, arguments):
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }
    }


class ToolSelectorTests(unittest.TestCase):
    def setUp(self):
        self.selector = ToolSelector(TOOL_REGISTRY)

    def test_audio_request_only_exposes_audio_domain(self):
        names = {spec.name for spec in self.selector.specs_for("abaixa o volume")}
        self.assertEqual(names, {
            "get_volume", "set_volume", "change_volume", "set_mute",
            "audio_input_status", "repair_audio_input",
        })

    def test_diagnostic_includes_multiple_read_domains(self):
        names = {spec.name for spec in self.selector.specs_for("meu PC está lento, diagnostica")}
        self.assertIn("system_status", names)
        self.assertIn("list_processes", names)
        self.assertIn("network_status", names)
        self.assertIn("ai_runtime_status", names)
        self.assertNotIn("delete_file", names)

    def test_notepad_workflow_includes_apps_files_and_desktop(self):
        names = {spec.name for spec in self.selector.specs_for("abre o Bloco de Notas, escreve e salva")}
        self.assertIn("open_app", names)
        self.assertIn("create_text_file", names)
        self.assertIn("type_text", names)

    def test_programming_followup_exposes_safe_code_tools(self):
        names = {
            spec.name for spec in self.selector.specs_for(
                "trabalhe com o CSS e JavaScript desse formulário HTML"
            )
        }
        self.assertIn("read_text_file", names)
        self.assertIn("create_text_file", names)
        self.assertIn("rewrite_text_file", names)
        self.assertIn("replace_text_in_file", names)

    def test_natural_revision_followup_exposes_code_tools(self):
        names = {
            spec.name for spec in self.selector.specs_for(
                "Está centralizado não, dê uma revisão e ajuste"
            )
        }
        self.assertIn("read_text_file", names)
        self.assertIn("rewrite_text_file", names)

        pronoun_names = {
            spec.name for spec in self.selector.specs_for(
                "Então, centralize ele no meio da tela"
            )
        }
        self.assertIn("read_text_file", pronoun_names)
        self.assertIn("rewrite_text_file", pronoun_names)
        self.assertLess(len(pronoun_names), 20)

        visual_names = {
            spec.name for spec in self.selector.specs_for(
                "Deixe a cor de fundo mais escura"
            )
        }
        self.assertIn("find_files", visual_names)
        self.assertIn("read_text_file", visual_names)
        self.assertIn("rewrite_text_file", visual_names)
        self.assertLess(len(visual_names), 20)

    def test_programming_request_uses_coordinated_agent_flow(self):
        skill = SimpleNamespace(_normalize=NexoActionsSkill._normalize)
        self.assertTrue(NexoActionsSkill._looks_like_multi_action(
            skill, "trabalhe com o CSS e JS desse formulário"
        ))
        self.assertFalse(NexoActionsSkill._looks_like_multi_action(
            skill, "leia o formulário .html em Downloads"
        ))
        self.assertTrue(NexoActionsSkill._looks_like_multi_action(
            skill,
            "Sabe aquele formulário que a gente fez? Está lá em index.html "
            "em download. Eu quero que você deixe o fundo dele mais escuro, "
            "um degradê bem mais sólido.",
        ))

        followup = SimpleNamespace(
            _normalize=NexoActionsSkill._normalize,
            conversation=SimpleNamespace(
                reference_context=lambda: "last_path=C:\\Downloads\\styles.css"
            ),
        )
        self.assertTrue(NexoActionsSkill._looks_like_action(
            followup, "Está centralizado não, dê uma revisão e ajuste"
        ))
        self.assertTrue(NexoActionsSkill._looks_like_multi_action(
            followup, "Está centralizado não, dê uma revisão e ajuste"
        ))
        implicit = "Não, eu quero um gradiente mais claro"
        self.assertTrue(NexoActionsSkill._looks_like_action(followup, implicit))
        self.assertTrue(NexoActionsSkill._looks_like_multi_action(followup, implicit))

    def test_stt_tirei_is_repaired_only_for_clear_development_request(self):
        skill = SimpleNamespace(_normalize=NexoActionsSkill._normalize)
        repair = NexoActionsSkill._repair_development_transcription
        fixed = repair(
            skill,
            "Tirei um formulário pra mim, bem bonitinho, com CSS, JavaScript, animado ali",
        )
        self.assertTrue(fixed.startswith("Crie um formulário"))
        self.assertTrue(NexoActionsSkill._looks_like_multi_action(skill, fixed))
        self.assertEqual(
            repair(skill, "Tirei uma foto ontem"),
            "Tirei uma foto ontem",
        )

    def test_e_ai_reports_active_agent_instead_of_claiming_completion(self):
        skill = SimpleNamespace(
            _normalize=NexoActionsSkill._normalize,
            job_manager=SimpleNamespace(list_jobs=lambda: [{
                "state": "running",
                "created_at": 10,
                "progress_message": "Lendo o arquivo correto",
            }]),
        )

        answer = NexoActionsSkill._active_task_status_response(skill, "E aí?")

        self.assertIn("Ainda estou trabalhando", answer)
        self.assertIn("Lendo o arquivo correto", answer)

    def test_chat_cannot_claim_an_action_without_a_tool(self):
        guarded = NexoActionsSkill._guard_chat_execution_claim(
            "Entendi, mudei o degradê para tons mais claros agora."
        )
        self.assertIn("não tenho uma ferramenta de gravação", guarded)

    def test_verification_uses_execution_evidence_instead_of_model_opinion(self):
        skill = SimpleNamespace(
            _normalize=NexoActionsSkill._normalize,
            _last_execution_evidence={
                "status": "applied",
                "paths": ["C:\\Users\\Administrador\\Downloads\\style.css"],
                "at": 100.0,
            },
        )
        clock = __import__("nexo_actions.skill", fromlist=["time"]).time
        original = clock.monotonic
        try:
            clock.monotonic = lambda: 101.0
            answer = NexoActionsSkill._execution_verification_response(
                skill,
                "Tem certeza que você mudou?",
            )
        finally:
            clock.monotonic = original
        self.assertIn("gravação confirmada", answer)
        self.assertIn("style.css", answer)

    def test_semantic_computer_side_effect_requires_confirmation(self):
        skill = SimpleNamespace(
            nexo_config={"safety": {"confirm_medium_risk": False}},
        )
        spec = TOOL_REGISTRY.get("computer_action")
        self.assertFalse(NexoActionsSkill._needs_confirmation(
            skill, spec, {"action": "play_pause", "sensitive": False}
        ))
        self.assertTrue(NexoActionsSkill._needs_confirmation(
            skill,
            spec,
            {
                "action": "click",
                "sensitive": True,
                "confirmation_reason": "enviar o formulário",
            },
        ))

    def test_semantic_authorization_only_skips_bounded_redundant_confirmation(self):
        skill = SimpleNamespace(
            nexo_config={"safety": {"confirm_medium_risk": False}},
        )
        spec = TOOL_REGISTRY.get("computer_action")
        check = NexoActionsSkill._needs_confirmation
        task_utterance = "Nexo, envie agora essa tarefa para o Codex"
        self.assertFalse(check(skill, spec, {
            "action": "click",
            "sensitive": True,
            "authorization": "explicit_now",
            "authorization_evidence": task_utterance,
            "effect_kind": "communication",
            "communication_scope": "direct",
            "communication_target": "Codex",
            "communication_content": "essa tarefa",
        }, task_utterance))
        self.assertFalse(check(skill, spec, {
            "action": "key",
            "key": "enter",
            "sensitive": True,
            "authorization": "explicit_now",
            "authorization_evidence": "consulte os processos",
            "effect_kind": "terminal_read",
        }, "Consulte os processos no terminal"))
        self.assertTrue(check(skill, spec, {
            "action": "click",
            "sensitive": False,
            "authorization": "not_explicit",
            "effect_kind": "communication",
        }))
        self.assertTrue(check(skill, spec, {
            "action": "key",
            "sensitive": False,
            "authorization": "not_explicit",
            "effect_kind": "terminal_read",
        }))
        for effect_kind in (
            "terminal_write", "destructive", "financial", "account", "credential",
        ):
            with self.subTest(effect_kind=effect_kind):
                self.assertTrue(check(skill, spec, {
                    "action": "key",
                    "sensitive": False,
                    "authorization": "explicit_now",
                    "effect_kind": effect_kind,
                }))

    def test_exact_current_whatsapp_send_skips_only_redundant_confirmation(self):
        skill = SimpleNamespace(
            nexo_config={"safety": {"confirm_medium_risk": False}},
        )
        spec = TOOL_REGISTRY.get("computer_action")
        check = NexoActionsSkill._needs_confirmation
        utterance = (
            'Nexo, abra o WhatsApp na conversa da Manuela e envie '
            'a mensagem "oi do Nexo".'
        )
        exact = {
            "action": "key",
            "key": "enter",
            "observation_id": "whatsapp-focused-composer",
            "sensitive": True,
            "authorization": "explicit_now",
            "authorization_evidence": utterance,
            "effect_kind": "communication",
            "communication_scope": "direct",
            "communication_target": "Manuela",
            "communication_content": "oi do Nexo",
        }
        self.assertFalse(check(skill, spec, exact, utterance))

        for changed in (
            {"authorization": "not_explicit", "authorization_evidence": ""},
            {"authorization_evidence": "envie a mensagem"},
            {"communication_target": "Mariana"},
            {"communication_content": "conteúdo inferido"},
            {"communication_scope": "bulk"},
            {"communication_scope": "sensitive"},
        ):
            with self.subTest(changed=changed):
                self.assertTrue(check(skill, spec, {**exact, **changed}, utterance))

    def test_enter_and_multiline_text_default_to_confirmation_when_metadata_lies(self):
        skill = SimpleNamespace(
            nexo_config={"safety": {"confirm_medium_risk": False}},
        )
        spec = TOOL_REGISTRY.get("computer_action")
        check = NexoActionsSkill._needs_confirmation
        self.assertTrue(check(skill, spec, {
            "action": "key",
            "key": "enter",
            "sensitive": False,
            "authorization": "explicit_now",
            "authorization_evidence": "trecho inventado",
            "effect_kind": "communication",
        }, "apenas preencha o campo"))
        self.assertTrue(check(skill, spec, {
            "action": "key",
            "key": "enter",
            "sensitive": False,
            "authorization": "explicit_now",
            "authorization_evidence": "apenas preencha o campo",
            "effect_kind": "other",
        }, "apenas preencha o campo"))
        self.assertTrue(check(skill, spec, {
            "action": "hotkey",
            "keys": ["ctrl", "enter"],
            "sensitive": False,
            "effect_kind": "navigation",
        }, "prepare a mensagem"))
        self.assertTrue(check(skill, spec, {
            "action": "type_text",
            "text": "consulta segura\nsegunda linha",
            "sensitive": False,
            "effect_kind": "other",
        }, "digite a consulta"))

    def test_system_prompt_defines_persistent_cross_app_visual_contract(self):
        for marker in (
            "contexto visual persistente",
            "abrir o Codex",
            "Chrome/YouTube",
            "localizar e ler e-mails",
            "terminal_read",
            "authorization=explicit_now",
            "observar -> agir -> observar",
            "dados não confiáveis",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, SYSTEM_PROMPT)

    def test_agent_completion_records_successful_file_mutation(self):
        spoken = []
        utterance = "Deixe a cor de fundo mais clara"
        skill = SimpleNamespace(
            _normalize=NexoActionsSkill._normalize,
            _progress_spoken=set(),
            _last_execution_evidence={
                "request": NexoActionsSkill._normalize(utterance),
                "status": "pending",
                "job_id": "job-1",
                "at": 1.0,
            },
            speak=lambda text, **kwargs: spoken.append((text, kwargs)),
            _request_confirmation=lambda *args, **kwargs: None,
        )
        job = {
            "job_id": "job-1",
            "state": "completed",
            "result": {
                "complete": True,
                "steps": [{"name": "replace_text_in_file"}],
                "tool_results": [{
                    "name": "replace_text_in_file",
                    "result": {
                        "ok": True,
                        "path": "C:\\Users\\Administrador\\Downloads\\style.css",
                    },
                }],
                "answer": "Gradiente atualizado.",
            },
        }

        NexoActionsSkill._on_agent_complete(skill, job, utterance)

        self.assertEqual(skill._last_execution_evidence["status"], "applied")
        self.assertEqual(
            skill._last_execution_evidence["paths"],
            ["C:\\Users\\Administrador\\Downloads\\style.css"],
        )
        self.assertEqual(spoken[0][0], "Gradiente atualizado.")

    def test_agent_cannot_report_edit_complete_without_mutation(self):
        spoken = []
        utterance = "Deixe a cor de fundo mais clara"
        skill = SimpleNamespace(
            _normalize=NexoActionsSkill._normalize,
            _progress_spoken=set(),
            _last_execution_evidence={
                "request": NexoActionsSkill._normalize(utterance),
                "status": "pending",
                "job_id": "job-2",
                "at": 1.0,
            },
            speak=lambda text, **kwargs: spoken.append((text, kwargs)),
            _request_confirmation=lambda *args, **kwargs: None,
        )
        job = {
            "job_id": "job-2",
            "state": "completed",
            "result": {
                "complete": True,
                "steps": [{"name": "read_text_file"}],
                "tool_results": [{
                    "name": "read_text_file",
                    "result": {
                        "ok": True,
                        "path": "C:\\Users\\Administrador\\Downloads\\style.css",
                        "content": "body { background: #2c3e50; }",
                    },
                }],
                "answer": "Pronto, deixei o fundo mais claro.",
            },
        }

        NexoActionsSkill._on_agent_complete(skill, job, utterance)

        self.assertEqual(skill._last_execution_evidence["status"], "not_applied")
        self.assertIn("alteração não foi concluída", spoken[0][0])
        self.assertNotIn("Pronto", spoken[0][0])


class AgentOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.executed = []

        def handler_a(value):
            self.executed.append(("a", value))
            return {"a": value}

        def handler_b(value):
            self.executed.append(("b", value))
            return {"b": value}

        self.registry = ToolRegistry([
            ToolSpec("a", "A", schema("a"), handler_a, category="test"),
            ToolSpec("b", "B", schema("b"), handler_b, category="test", risk_level="medium"),
        ])
        self.selector = ToolSelector(self.registry)
        self.selector.CATEGORY_PATTERNS = {"test": r"."}

    def test_semantic_provider_receives_image_but_receipt_is_redacted(self):
        image = "aGVsbG8tdmlzaW9u"
        registry = ToolRegistry([
            ToolSpec(
                "observe",
                "Observe",
                {"type": "object", "properties": {}, "additionalProperties": False},
                lambda: {"ok": True, "image_base64": image, "observation_id": "obs-1"},
                category="test",
            )
        ])
        client = SemanticFakeClient([
            call_with_arguments("observe", {}),
            {"message": {"role": "assistant", "content": "Imagem analisada.", "tool_calls": []}},
        ])
        agent = AgentOrchestrator(registry, client=client, max_steps=2)

        result = agent.run("Observe a tela")

        tool_message = [
            item for item in client.calls[1]["messages"] if item.get("role") == "tool"
        ][0]
        self.assertIn(image, tool_message["content"])
        self.assertEqual(
            result["tool_results"][0]["result"]["image_base64"],
            "<redacted>",
        )

    def test_openai_web_sources_are_bounded_and_link_metadata_only(self):
        candidates = [
            {
                "title": f"  Fonte   {index}  ",
                "url": f"https://example{index}.com/page?q={index}#fragment",
                "snippet": "conteúdo não deve ser propagado",
            }
            for index in range(8)
        ]
        candidates.extend([
            {"title": "Unsafe", "url": "file:///etc/passwd"},
            {"title": "Credential", "url": "https://user:secret@example.com/private"},
        ])
        client = SemanticFakeClient([{
            "message": {
                "role": "assistant",
                "content": "Encontrei fontes atuais.",
                "tool_calls": [],
            },
            "nexo_meta": {"sources": candidates},
        }])
        agent = AgentOrchestrator(
            self.registry,
            client=client,
            max_steps=2,
            timeout_seconds=20,
        )

        result = agent.run("pesquise fontes atuais")

        self.assertTrue(result["complete"])
        self.assertEqual(len(result["sources"]), 6)
        self.assertEqual(set(result["sources"][0]), {"title", "url"})
        self.assertEqual(result["sources"][0]["title"], "Fonte 0")
        self.assertNotIn("#fragment", result["sources"][0]["url"])
        self.assertNotIn("conteúdo", str(result["sources"]))

    def test_multi_tool_loop_executes_results_then_answers(self):
        client = FakeClient([
            call("a", 2),
            call("b", 3),
            {"message": {"role": "assistant", "content": "Tudo certo."}},
        ])
        agent = AgentOrchestrator(
            self.registry, selector=self.selector, client=client, max_steps=4, timeout_seconds=20
        )
        result = agent.run("faça o teste")
        self.assertTrue(result["complete"])
        self.assertEqual([step["name"] for step in result["steps"]], ["a", "b"])
        self.assertEqual(self.executed, [("a", 2), ("b", 3)])
        self.assertEqual(result["answer"], "Tudo certo.")

    def test_repeated_call_is_detected_and_not_executed_twice(self):
        client = FakeClient([
            call("a", 1),
            call("a", 1),
            {"message": {"role": "assistant", "content": "Usei o primeiro resultado."}},
        ])
        agent = AgentOrchestrator(
            self.registry, selector=self.selector, client=client, max_steps=4, timeout_seconds=20
        )
        result = agent.run("teste")
        self.assertTrue(result["loop_detected"])
        self.assertEqual(self.executed, [("a", 1)])

    def test_duplicate_responses_call_is_closed_before_finalization(self):
        class StrictResponsesTransport:
            def __init__(self):
                self.payloads = []

            def __call__(self, payload, _timeout):
                self.payloads.append(payload)
                turn = len(self.payloads)
                if turn == 1:
                    return {
                        "id": "resp_1",
                        "model": "gpt-test",
                        "status": "completed",
                        "output": [{
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "a",
                            "arguments": '{"value":1}',
                        }],
                        "usage": {},
                    }

                expected_call_id = "call_1" if turn == 2 else "call_2"
                outputs = {
                    str(item.get("call_id") or ""): item
                    for item in payload.get("input") or []
                    if item.get("type") == "function_call_output"
                }
                if expected_call_id not in outputs:
                    raise ProviderError(
                        "No tool output found for function call "
                        + expected_call_id
                    )
                if turn == 2:
                    return {
                        "id": "resp_2",
                        "model": "gpt-test",
                        "status": "completed",
                        "output": [{
                            "type": "function_call",
                            "call_id": "call_2",
                            "name": "a",
                            "arguments": '{"value":1}',
                        }],
                        "usage": {},
                    }
                return {
                    "id": "resp_3",
                    "model": "gpt-test",
                    "status": "completed",
                    "output_text": "Usei o primeiro resultado.",
                    "output": [],
                    "usage": {},
                }

        transport = StrictResponsesTransport()
        client = OpenAIResponsesClient(
            model="gpt-test",
            transport=transport,
            web_search=False,
            max_retries=0,
            usage_ledger=SimpleNamespace(record=lambda **_kwargs: None),
        )
        agent = AgentOrchestrator(
            self.registry,
            client=client,
            max_steps=4,
            timeout_seconds=20,
        )

        result = agent.run("teste")

        self.assertTrue(result["loop_detected"])
        self.assertEqual(result["answer"], "Usei o primeiro resultado.")
        self.assertEqual(self.executed, [("a", 1)])
        self.assertEqual(len(transport.payloads), 3)
        duplicate_output = next(
            item
            for item in transport.payloads[2]["input"]
            if item.get("type") == "function_call_output"
        )
        self.assertEqual(duplicate_output["call_id"], "call_2")
        self.assertIn("duplicate_call", duplicate_output["output"])

    def test_read_tool_can_repeat_after_successful_state_change(self):
        executed = []
        observations = iter(("before", "after"))
        empty_schema = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

        def observe():
            observation_id = next(observations)
            executed.append(("observe", observation_id))
            return {"ok": True, "observation_id": observation_id}

        def act():
            executed.append(("action", None))
            return {"ok": True, "verified": True}

        registry = ToolRegistry([
            ToolSpec(
                "observe_desktop",
                "Observa a tela",
                empty_schema,
                observe,
                category="desktop",
                risk_level="read_only",
            ),
            ToolSpec(
                "computer_action",
                "Age na tela",
                empty_schema,
                act,
                category="desktop",
                risk_level="medium",
            ),
        ])
        client = SemanticFakeClient([
            call_with_arguments("observe_desktop", {}),
            call_with_arguments("computer_action", {}),
            call_with_arguments("observe_desktop", {}),
            {"message": {"role": "assistant", "content": "A??o verificada."}},
        ])
        agent = AgentOrchestrator(
            registry,
            client=client,
            max_steps=4,
            timeout_seconds=20,
        )

        result = agent.run("observe, fa?a a a??o e confira o resultado")

        self.assertTrue(result["complete"])
        self.assertTrue(result["verified"])
        self.assertFalse(result["loop_detected"])
        self.assertEqual(
            executed,
            [("observe", "before"), ("action", None), ("observe", "after")],
        )

    def test_action_budget_closes_every_call_in_multi_call_response(self):
        class StrictMultiCallTransport:
            def __init__(self):
                self.payloads = []

            def __call__(self, payload, _timeout):
                self.payloads.append(payload)
                if len(self.payloads) == 1:
                    return {
                        "id": "resp_multi",
                        "model": "gpt-test",
                        "status": "completed",
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_a",
                                "name": "a",
                                "arguments": '{"value":1}',
                            },
                            {
                                "type": "function_call",
                                "call_id": "call_b",
                                "name": "b",
                                "arguments": '{"value":2}',
                            },
                        ],
                        "usage": {},
                    }
                outputs = {
                    str(item.get("call_id") or ""): item
                    for item in payload.get("input") or []
                    if item.get("type") == "function_call_output"
                }
                missing = {"call_a", "call_b"} - set(outputs)
                if missing:
                    raise ProviderError(
                        "No tool output found for function call "
                        + sorted(missing)[0]
                    )
                return {
                    "id": "resp_final",
                    "model": "gpt-test",
                    "status": "completed",
                    "output_text": "Executei apenas a etapa poss?vel.",
                    "output": [],
                    "usage": {},
                }

        transport = StrictMultiCallTransport()
        client = OpenAIResponsesClient(
            model="gpt-test",
            transport=transport,
            web_search=False,
            max_retries=0,
            usage_ledger=SimpleNamespace(record=lambda **_kwargs: None),
        )
        agent = AgentOrchestrator(
            self.registry,
            client=client,
            max_steps=1,
            timeout_seconds=20,
        )

        result = agent.run("fa?a as duas etapas")

        self.assertFalse(result["complete"])
        self.assertFalse(result["verified"])
        self.assertEqual(self.executed, [("a", 1)])
        outputs = {
            item["call_id"]: item["output"]
            for item in transport.payloads[1]["input"]
            if item.get("type") == "function_call_output"
        }
        self.assertEqual(set(outputs), {"call_a", "call_b"})
        self.assertIn("action_budget_exhausted", outputs["call_b"])

    def test_confirmation_stops_before_side_effect(self):
        client = FakeClient([call("b", 4)])
        agent = AgentOrchestrator(
            self.registry, selector=self.selector, client=client, timeout_seconds=20
        )
        result = agent.run("teste", requires_confirmation=lambda spec, _args: spec.risk_level == "medium")
        self.assertFalse(result["complete"])
        self.assertEqual(result["pending_confirmation"]["name"], "b")
        self.assertEqual(self.executed, [])

    def test_tool_results_redact_secrets(self):
        compact = AgentOrchestrator._compact_result({"token": "secret", "nested": {"password": "x"}})
        self.assertEqual(compact["token"], "<redacted>")
        self.assertEqual(compact["nested"]["password"], "<redacted>")

    def test_computer_action_requires_fresh_visual_observation(self):
        steps = [
            {"name": "observe_desktop"},
            {"name": "computer_action"},
        ]
        results = [
            {"result": {"ok": True, "observation_id": "before"}},
            {"result": {"ok": True, "verified": True}},
        ]
        self.assertFalse(AgentOrchestrator._results_verified(steps, results))

        steps.append({"name": "observe_desktop"})
        results.append({"result": {"ok": True, "observation_id": "after"}})
        self.assertTrue(AgentOrchestrator._results_verified(steps, results))

    def test_open_app_requires_window_verification(self):
        steps = [{"name": "open_app"}]
        self.assertFalse(AgentOrchestrator._results_verified(
            steps,
            [{"result": {"ok": True, "dispatched": True, "verified": False}}],
        ))
        self.assertTrue(AgentOrchestrator._results_verified(
            steps,
            [{"result": {"ok": True, "dispatched": True, "verified": True}}],
        ))

    def test_exact_filename_and_quoted_note_are_preserved(self):
        constrained = AgentOrchestrator._constrain_arguments(
            "Escreva 'reunião amanhã às nove' em Downloads como lembrete-nexo.txt",
            "create_text_file",
            {"directory": "Documentos", "filename": "lembrete-nex0.txt", "content": "texto errado"},
        )
        self.assertEqual(constrained["directory"], "downloads")
        self.assertEqual(constrained["filename"], "lembrete-nexo.txt")
        self.assertEqual(constrained["content"], "reunião amanhã às nove")

    def test_file_search_glob_is_converted_to_supported_extension_filter(self):
        constrained = AgentOrchestrator._constrain_arguments(
            "Altere a cor de fundo do site",
            "find_files",
            {"query": "*.css", "scope": "Downloads"},
        )
        self.assertEqual(constrained["query"], "")
        self.assertEqual(constrained["extension"], "css")
        self.assertEqual(constrained["scope"], "downloads")

    def test_visual_detection_does_not_confuse_corrigir_with_cor(self):
        self.assertTrue(AgentOrchestrator._is_visual_edit_request(
            "Troque a cor de fundo do site",
        ))
        self.assertFalse(AgentOrchestrator._is_visual_edit_request(
            "Corrija o JavaScript do formulário",
        ))

    def test_visual_edit_promotes_unique_search_result_to_read(self):
        executed = []
        path = "C:\\Users\\Administrador\\Downloads\\style.css"

        find_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {"type": "string"},
                "extension": {"type": "string"},
            },
            "additionalProperties": False,
        }
        read_schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        }
        write_schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "expected_sha256": {"type": "string"},
            },
            "required": ["path", "content", "expected_sha256"],
            "additionalProperties": False,
        }

        registry = ToolRegistry([
            ToolSpec(
                "find_files", "Procura", find_schema,
                lambda query="", scope="", extension="": (
                    executed.append(("find", query, extension, scope))
                    or {
                        "ok": True,
                        "root": "C:\\Users\\Administrador\\Downloads",
                        "files": [{"name": "style.css", "path": path}],
                    }
                ),
                category="files",
            ),
            ToolSpec(
                "read_text_file", "Lê", read_schema,
                lambda path: (
                    executed.append(("read", path))
                    or {"ok": True, "path": path, "content": "body {}", "sha256": "abc"}
                ),
                category="files",
            ),
            ToolSpec(
                "rewrite_text_file", "Grava", write_schema,
                lambda path, content, expected_sha256: (
                    executed.append(("write", path))
                    or {"ok": True, "path": path}
                ),
                category="files",
            ),
        ])
        client = FakeClient([
            call_with_arguments("find_files", {"query": "*.css", "scope": "Downloads"}),
            # Simulate Gemma trying to search again; orchestration must promote
            # the already found candidate to read_text_file.
            call_with_arguments("find_files", {"query": "*form*", "scope": "Downloads"}),
            call_with_arguments("rewrite_text_file", {
                "path": path,
                "content": "body { background: #101827; }",
                "expected_sha256": "abc",
            }),
            {"message": {"role": "assistant", "content": "Fundo escurecido."}},
        ])
        agent = AgentOrchestrator(
            registry,
            selector=ToolSelector(registry),
            client=client,
            max_steps=6,
            timeout_seconds=20,
        )

        result = agent.run("Deixe a cor de fundo do site mais escura")

        self.assertTrue(result["complete"])
        self.assertEqual([step["name"] for step in result["steps"]], [
            "find_files", "read_text_file", "rewrite_text_file",
        ])
        self.assertEqual(executed, [
            ("find", "", "css", "downloads"),
            ("read", path),
            ("write", path),
        ])

    def test_new_web_project_defaults_to_existing_downloads_directory(self):
        constrained = AgentOrchestrator._constrain_arguments(
            "Crie um formulário HTML, CSS e JavaScript bem bonitinho",
            "create_text_file",
            {
                "directory": "C:\\Users\\Administrador\\Documents\\Nexo_Projects",
                "filename": "index.html",
                "content": "<html></html>",
            },
        )
        self.assertEqual(constrained["directory"], "downloads")

    def test_tool_failure_is_returned_to_model_for_recovery(self):
        def broken(value):
            raise FileNotFoundError("pasta inventada")

        registry = ToolRegistry([
            ToolSpec("broken", "Falha", schema("broken"), broken, category="test"),
            ToolSpec("a", "Alternativa", schema("a"), lambda value: {"a": value}, category="test"),
        ])
        selector = ToolSelector(registry)
        selector.CATEGORY_PATTERNS = {"test": r"."}
        client = FakeClient([
            call("broken", 1),
            call("a", 2),
            {"message": {"role": "assistant", "content": "Corrigi e concluí."}},
        ])
        agent = AgentOrchestrator(
            registry, selector=selector, client=client, max_steps=4, timeout_seconds=20
        )

        result = agent.run("faça o teste")

        self.assertTrue(result["complete"])
        self.assertTrue(result["verified"])
        self.assertEqual([step["name"] for step in result["steps"]], ["broken", "a"])
        self.assertEqual(result["tool_results"][0]["result"]["recoverable"], True)
        self.assertEqual(result["answer"], "Corrigi e concluí.")

    def test_malformed_semantic_tool_arguments_are_recoverable(self):
        client = SemanticFakeClient([
            {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {"name": "a", "arguments": "{invalid"},
                    }],
                },
            },
            call("a", 7),
            {"message": {"role": "assistant", "content": "Corrigi e concluí."}},
        ])
        agent = AgentOrchestrator(
            self.registry,
            client=client,
            max_steps=4,
            timeout_seconds=20,
        )

        result = agent.run("faça o teste")

        self.assertTrue(result["complete"])
        self.assertTrue(result["verified"])
        self.assertEqual(self.executed, [("a", 7)])
        self.assertTrue(result["tool_results"][0]["result"]["not_executed"])

    def test_invalid_call_followed_only_by_success_claim_is_incomplete(self):
        client = SemanticFakeClient([
            {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {"name": "a", "arguments": "{invalid"},
                    }],
                },
            },
            {"message": {"role": "assistant", "content": "Pronto, foi concluído."}},
        ])
        agent = AgentOrchestrator(
            self.registry,
            client=client,
            max_steps=3,
            timeout_seconds=20,
        )

        result = agent.run("faça o teste")

        self.assertFalse(result["complete"])
        self.assertFalse(result["verified"])
        self.assertEqual(self.executed, [])
        self.assertTrue(result["tool_results"][0]["result"]["not_executed"])

    def test_unknown_tool_and_invalid_schema_are_recoverable(self):
        client = SemanticFakeClient([
            call("outside_catalog", 1),
            call_with_arguments("a", {}),
            call("a", 9),
            {"message": {"role": "assistant", "content": "Recuperei e concluí."}},
        ])
        agent = AgentOrchestrator(
            self.registry,
            client=client,
            max_steps=5,
            timeout_seconds=20,
        )

        result = agent.run("faça o teste")

        self.assertTrue(result["complete"])
        self.assertTrue(result["verified"])
        self.assertEqual(self.executed, [("a", 9)])
        self.assertEqual(
            [item["result"]["not_executed"] for item in result["tool_results"][:2]],
            [True, True],
        )

    def test_empty_model_output_is_incomplete_instead_of_false_success(self):
        client = SemanticFakeClient([
            {"message": {"content": "", "tool_calls": []}},
            {"message": {"content": "", "tool_calls": []}},
            {"message": {"content": "", "tool_calls": []}},
        ])
        agent = AgentOrchestrator(
            self.registry,
            client=client,
            max_steps=2,
            timeout_seconds=20,
        )

        result = agent.run("responda")

        self.assertFalse(result["complete"])
        self.assertFalse(result["verified"])
        self.assertIn("utilizável", result["answer"])

    def test_web_project_cannot_finish_before_html_css_and_js_exist(self):
        created = []

        def create(directory, filename, content, overwrite=False, open_after=False):
            created.append(filename)
            return {"ok": True, "path": f"{directory}/{filename}"}

        create_schema = {
            "type": "object",
            "properties": {
                "directory": {"type": "string"},
                "filename": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "open_after": {"type": "boolean"},
            },
            "required": ["directory", "filename", "content"],
            "additionalProperties": False,
        }
        registry = ToolRegistry([
            ToolSpec(
                "create_text_file",
                "Cria arquivo",
                create_schema,
                create,
                category="files",
            ),
        ])
        selector = ToolSelector(registry)
        client = FakeClient([
            call_with_arguments("create_text_file", {
                "directory": "Downloads",
                "filename": "index.html",
                "content": '<link href="styles.css"><script src="script.js"></script>',
            }),
            {"message": {"role": "assistant", "content": "Pronto."}},
            call_with_arguments("create_text_file", {
                "directory": "Downloads",
                "filename": "styles.css",
                "content": "form { display: grid; }",
            }),
            {"message": {"role": "assistant", "content": "Pronto."}},
            call_with_arguments("create_text_file", {
                "directory": "Downloads",
                "filename": "script.js",
                "content": "console.log('ok');",
            }),
            {"message": {"role": "assistant", "content": "Projeto concluído."}},
        ])
        agent = AgentOrchestrator(
            registry, selector=selector, client=client, max_steps=6, timeout_seconds=20
        )

        result = agent.run(
            "Crie um formulário HTML, CSS e JavaScript bem bonitinho"
        )

        self.assertTrue(result["complete"])
        self.assertEqual(created, ["index.html", "styles.css", "script.js"])
        self.assertEqual(result["answer"], "Projeto concluído.")

    def test_web_contract_infers_html_from_formulario_when_stt_omits_word(self):
        missing = AgentOrchestrator._missing_web_project_steps(
            "Crie um formulário pra mim com CSS e JavaScript",
            [],
            [],
        )
        self.assertEqual(
            missing,
            [
                "criar o arquivo HTML",
                "criar o arquivo CSS",
                "criar o arquivo JavaScript",
            ],
        )

    def test_local_edit_rejects_explanation_until_read_and_write_succeed(self):
        executed = []

        read_schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        }
        write_schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "expected_sha256": {"type": "string"},
            },
            "required": ["path", "content", "expected_sha256"],
            "additionalProperties": False,
        }

        def read_file(path):
            executed.append(("read", path))
            return {"ok": True, "path": path, "content": "*body {}", "sha256": "abc"}

        def rewrite_file(path, content, expected_sha256):
            executed.append(("write", path, expected_sha256))
            return {"ok": True, "path": path, "backup_path": path + ".bak"}

        registry = ToolRegistry([
            ToolSpec(
                "read_text_file", "Lê", read_schema, read_file,
                category="files",
            ),
            ToolSpec(
                "rewrite_text_file", "Reescreve", write_schema, rewrite_file,
                category="files",
            ),
        ])
        selector = ToolSelector(registry)
        path = "C:\\Users\\Administrador\\Downloads\\style.css"
        client = FakeClient([
            {"message": {"role": "assistant", "content": "Use Flexbox no body."}},
            call_with_arguments("read_text_file", {"path": path}),
            {"message": {"role": "assistant", "content": "Agora está pronto."}},
            call_with_arguments("rewrite_text_file", {
                "path": path,
                "content": "body { display: flex; justify-content: center; }",
                "expected_sha256": "abc",
            }),
            {"message": {"role": "assistant", "content": "Centralizei o formulário."}},
        ])
        agent = AgentOrchestrator(
            registry,
            selector=selector,
            client=client,
            max_steps=5,
            timeout_seconds=20,
        )

        result = agent.run(
            "Então, centralize ele no meio da tela",
            history=[{
                "role": "system",
                "content": "Referências atuais: last_path=" + path,
            }],
        )

        self.assertTrue(result["complete"])
        self.assertEqual([step["name"] for step in result["steps"]], [
            "read_text_file",
            "rewrite_text_file",
        ])
        self.assertEqual(executed[0], ("read", path))
        self.assertEqual(executed[1], ("write", path, "abc"))
        self.assertEqual(result["answer"], "Centralizei o formulário.")

        corrective_prompts = [
            item["content"]
            for call_data in client.calls
            for item in call_data["messages"]
            if item.get("role") == "system"
            and "edição real" in item.get("content", "")
        ]
        self.assertTrue(corrective_prompts)

    def test_css_validator_catches_invalid_star_body_selector(self):
        issues = AgentOrchestrator._local_code_diagnostics(
            "read_text_file",
            {"path": "C:\\Users\\Administrador\\Downloads\\style.css"},
            {
                "ok": True,
                "path": "C:\\Users\\Administrador\\Downloads\\style.css",
                "content": "*body { display: flex; }",
            },
        )
        self.assertEqual(len(issues), 1)
        self.assertIn("'*body'", issues[0])


if __name__ == "__main__":
    unittest.main()
