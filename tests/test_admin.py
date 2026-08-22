import json
import io
import stat
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace
import wave

from nexo_actions.admin import AdminAuth, AdminService, _trim_listening_prompt
from nexo_actions.usage import UsageLedger


class FakeAdminService(AdminService):
    def _ollama_models(self):
        return [{"id": "model-a", "size": 10}, {"id": "model-b", "size": 20}]

    def _remote_voice_catalog(self):
        return []


class AdminAuthTests(unittest.TestCase):
    def test_setup_login_session_csrf_and_logout(self):
        with tempfile.TemporaryDirectory() as folder:
            auth = AdminAuth(Path(folder) / "auth.json", session_ttl=120)
            self.assertFalse(auth.configured)
            session = auth.setup("uma-senha-segura")
            self.assertTrue(auth.configured)
            self.assertTrue(auth.validate(session["token"]))
            self.assertFalse(auth.validate(session["token"], "wrong", write=True))
            self.assertTrue(auth.validate(session["token"], session["csrf"], write=True))
            auth.logout(session["token"])
            self.assertFalse(auth.validate(session["token"]))
            logged = auth.login("uma-senha-segura")
            self.assertTrue(auth.validate(logged["token"]))
            self.assertEqual(stat.S_IMODE((Path(folder) / "auth.json").stat().st_mode), 0o600)

    def test_wrong_password_and_reconfiguration_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            auth = AdminAuth(Path(folder) / "auth.json")
            auth.setup("uma-senha-segura")
            with self.assertRaises(PermissionError):
                auth.login("senha-incorreta")
            with self.assertRaises(PermissionError):
                auth.setup("outra-senha-segura")


class AdminServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.config = root / "nexo.json"
        self.ovos = root / "mycroft.json"
        self.voice_root = root / "voices"
        self.voice_root.mkdir()
        self.voice = self.voice_root / "pt_BR-test-medium.onnx"
        self.voice.write_bytes(b"model")
        Path(str(self.voice) + ".json").write_text("{}", encoding="utf-8")
        self.config.write_text(json.dumps({
            "ollama": {"url": "http://localhost:11434/api/chat", "model": "model-a", "keep_alive": "30m"},
            "conversation": {"timeout_seconds": 180, "max_messages": 24, "max_tokens": 2200, "barge_in": True},
        }), encoding="utf-8")
        self.ovos.write_text(json.dumps({
            "tts": {"ovos-tts-plugin-phoonnx": {
                "model": str(self.voice), "model_config": str(self.voice) + ".json", "length-scale": 1.0,
            }},
            "stt": {"ovos-stt-plugin-fasterwhisper": {
                "model": "medium", "beam_size": 5, "use_cuda": True, "compute_type": "float16",
            }},
            "listener": {"fake_barge_in": True, "barge_in_volume": 20},
        }), encoding="utf-8")
        self.commands = []
        self.reloads = 0
        self.openai_prompt_calls = []

    def tearDown(self):
        self.folder.cleanup()

    def runner(self, command, **_kwargs):
        self.commands.append(command)
        if command[-2:] and "is-active" in command:
            return SimpleNamespace(returncode=0, stdout="active\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def service(self, runner=None, **overrides):
        prompt = io.BytesIO()
        with wave.open(prompt, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 160)
        options = {
            "openai_key_loader": lambda: "test-openai-key",
            "credential_status_loader": lambda: {
                "configured": True, "source": "test", "secure_storage": True,
            },
            "usage_ledger": UsageLedger(Path(self.folder.name) / "usage.jsonl"),
        }
        options.update(overrides)
        options.setdefault(
            "openai_prompt_renderer",
            lambda text, voice: (
                self.openai_prompt_calls.append((text, voice)) or prompt.getvalue()
            ),
        )
        return FakeAdminService(
            config_path=self.config,
            ovos_config_path=self.ovos,
            auth_path=Path(self.folder.name) / "auth.json",
            voice_root=self.voice_root,
            neural_tts_root=Path(self.folder.name) / "neural-tts",
            listening_prompt_root=Path(self.folder.name) / "listening-prompts",
            command_runner=runner or self.runner,
            reload_callback=lambda: setattr(self, "reloads", self.reloads + 1),
            prompt_renderer=lambda _text, _model, _scale: prompt.getvalue(),
            **options,
        )

    def test_catalog_and_settings_only_expose_installed_choices(self):
        service = self.service()
        try:
            catalog = service.catalog()
            settings = service.settings()
            self.assertEqual(catalog["voices"][0]["quality"], "medium")
            self.assertEqual([model["id"] for model in catalog["ai_models"]], ["model-a", "model-b"])
            self.assertEqual(settings["ai"]["provider"], "openai")
            openai = settings["ai"]["profiles"]["openai"]
            self.assertEqual(openai["agent_model"], "gpt-5.6-sol")
            self.assertEqual(openai["routine_agent_model"], "gpt-5.6-terra")
            self.assertEqual(openai["routine_reasoning_effort"], "none")
            self.assertEqual(openai["realtime_reasoning_effort"], "minimal")
            self.assertEqual(openai["realtime_response_style"], "concise")
            self.assertEqual(openai["realtime_max_output_tokens"], 1024)
            self.assertEqual(openai["agent_text_verbosity"], "low")
            self.assertEqual(
                catalog["ai"]["realtime_reasoning_efforts"][0]["id"],
                "minimal",
            )
            self.assertEqual(
                catalog["ai"]["routine_reasoning_efforts"][0]["id"],
                "none",
            )
            self.assertEqual(settings["ai"]["profiles"]["ollama"]["model"], "model-a")
            self.assertEqual(catalog["ai"]["credential"]["configured"], True)
            self.assertEqual(catalog["usage"]["today"]["requests"], 0)
            self.assertNotIn("key", json.dumps(catalog).casefold())
        finally:
            service.close()

    def test_apply_is_atomic_selective_and_reloads_skill(self):
        service = self.service()
        try:
            settings = service.settings()
            settings["ai"]["profiles"]["ollama"]["model"] = "model-b"
            settings["stt"]["model"] = "small"
            settings["conversation"]["barge_in_volume_percent"] = 25
            normalized = service._validate_settings(settings)
            context = SimpleNamespace(
                report_progress=lambda *_args, **_kwargs: None,
                raise_if_cancelled=lambda: None,
            )
            result = service._apply(normalized, job_context=context)
            self.assertTrue(result["ok"])
            written = json.loads(self.config.read_text())
            self.assertEqual(written["ollama"]["model"], "model-b")
            self.assertEqual(written["ai"]["provider"], "openai")
            self.assertFalse(written["ai"]["fallback_enabled"])
            self.assertEqual(written["ai"]["profiles"]["ollama"]["model"], "model-b")
            self.assertEqual(json.loads(self.ovos.read_text())["stt"]["nexo-smart-stt"]["model"], "small")
            restarted = [command[-1] for command in self.commands if "restart" in command]
            self.assertIn("ovos-listener.service", restarted)
            self.assertNotIn("ovos-audio.service", restarted)
            ovos = json.loads(self.ovos.read_text())
            self.assertFalse(ovos["listener"]["instant_listen"])
            self.assertEqual(ovos["listener"]["speech_begin"], 0.30)
            self.assertEqual(ovos["listener"]["silence_end"], 0.90)
            self.assertEqual(ovos["stt"]["module"], "nexo-smart-stt")
            self.assertTrue(ovos["stt"]["nexo-smart-stt"]["vad_filter"])
            prompt_path = Path(ovos["hotwords"]["nexo"]["sound"])
            self.assertTrue(prompt_path.is_file())
            self.assertTrue(prompt_path.name.startswith("manda-"))
            self.assertIn(("Manda.", "marin"), self.openai_prompt_calls)
            self.assertEqual(self.reloads, 1)
        finally:
            service.close()

    def test_listening_prompt_removes_hidden_padding(self):
        source = io.BytesIO()
        with wave.open(source, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 1600)
            audio.writeframes((1000).to_bytes(2, "little", signed=True) * 4800)
            audio.writeframes(b"\x00\x00" * 2400)
        trimmed = _trim_listening_prompt(source.getvalue())
        with wave.open(io.BytesIO(trimmed), "rb") as audio:
            self.assertLess(audio.getnframes(), 6500)
            self.assertGreater(audio.getnframes(), 5500)

    def test_openai_voice_selection_changes_the_listening_prompt_voice(self):
        service = self.service()
        try:
            settings = service.settings()
            settings["ai"]["profiles"]["openai"]["voice"] = "cedar"
            normalized = service._validate_settings(settings)
            context = SimpleNamespace(
                report_progress=lambda *_args, **_kwargs: None,
                raise_if_cancelled=lambda: None,
            )
            result = service._apply(normalized, job_context=context)
            self.assertTrue(result["ok"])
            self.assertIn(("Manda.", "cedar"), self.openai_prompt_calls)
            written = json.loads(self.ovos.read_text())
            self.assertIn("manda-", written["hotwords"]["nexo"]["sound"])
        finally:
            service.close()

    def test_neural_voice_is_downloadable_then_selectable_with_piper_fallback(self):
        service = self.service()
        marker = service.neural_tts_root / "installed.json"
        try:
            before = next(item for item in service.catalog()["voices"] if item["id"] == "neural:ptbr-natural")
            self.assertFalse(before["installed"])
            marker.parent.mkdir(parents=True)
            marker.write_text("{}", encoding="utf-8")
            after = next(item for item in service.catalog()["voices"] if item["id"] == "neural:ptbr-natural")
            self.assertTrue(after["installed"])
            settings = service.settings()
            settings["tts"]["model"] = "neural:ptbr-natural"
            normalized = service._validate_settings(settings)
            context = SimpleNamespace(
                report_progress=lambda *_args, **_kwargs: None,
                raise_if_cancelled=lambda: None,
            )
            service._apply(normalized, job_context=context)
            ovos = json.loads(self.ovos.read_text())
            self.assertEqual(ovos["tts"]["module"], "nexo-hybrid-tts")
            hybrid = ovos["tts"]["nexo-hybrid-tts"]
            self.assertEqual(hybrid["voice"], "neural:ptbr-natural")
            self.assertEqual(hybrid["fallback_model"], str(self.voice))
            self.assertIn("manda-", ovos["hotwords"]["nexo"]["sound"])
        finally:
            service.close()

    def test_failed_restart_rolls_both_configs_back(self):
        old_config = self.config.read_text()
        old_ovos = self.ovos.read_text()

        def failing_runner(command, **_kwargs):
            if "restart" in command:
                return SimpleNamespace(returncode=1, stdout="", stderr="boom")
            return SimpleNamespace(returncode=0, stdout="active\n", stderr="")

        service = self.service(failing_runner)
        try:
            settings = service.settings()
            settings["tts"]["length_scale"] = 1.2
            normalized = service._validate_settings(settings)
            context = SimpleNamespace(report_progress=lambda *_args, **_kwargs: None, raise_if_cancelled=lambda: None)
            with self.assertRaises(RuntimeError):
                service._apply(normalized, job_context=context)
            self.assertEqual(json.loads(self.config.read_text()), json.loads(old_config))
            self.assertEqual(json.loads(self.ovos.read_text()), json.loads(old_ovos))
        finally:
            service.close()

    def test_provider_validation_preserves_openai_and_disabled_local_fallback(self):
        service = self.service()
        try:
            settings = service.settings()
            settings["ai"]["provider"] = "openai"
            settings["ai"]["fallback_enabled"] = False
            settings["ai"]["profiles"]["openai"].update({
                "agent_model": "gpt-5.6-sol",
                "realtime_model": "gpt-realtime-2.1",
                "voice": "marin",
                "reasoning_effort": "medium",
                "agent_text_verbosity": "low",
                "routine_reasoning_effort": "none",
                "routine_agent_model": "gpt-5.6-terra",
                "realtime_reasoning_effort": "minimal",
                "realtime_max_output_tokens": 1024,
                "realtime_response_style": "concise",
            })
            normalized = service._validate_settings(settings)
            self.assertEqual(normalized["ai"]["provider"], "openai")
            self.assertFalse(normalized["ai"]["fallback_enabled"])
            self.assertEqual(
                normalized["ai"]["profiles"]["openai"]["realtime_reasoning_effort"],
                "minimal",
            )
            self.assertEqual(
                normalized["ai"]["profiles"]["openai"]["routine_reasoning_effort"],
                "none",
            )
            with self.assertRaises(ValueError):
                settings["ai"]["profiles"]["openai"]["agent_model"] = "modelo-inventado"
                service._validate_settings(settings)
        finally:
            service.close()

    def test_openai_connection_status_never_returns_credential(self):
        service = self.service(openai_key_loader=lambda: "super-secret-test-key")
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "data": [{"id": "gpt-5.6-sol"}],
        }).encode()
        try:
            with mock.patch("nexo_actions.admin.urllib.request.urlopen", return_value=response):
                status = service.test_provider("openai")
            encoded = json.dumps(status)
            self.assertTrue(status["ok"])
            self.assertTrue(status["configured_model_visible"])
            self.assertNotIn("super-secret", encoded)
            self.assertNotIn("api_key", encoded.casefold())
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
