import json
import tempfile
import unittest
import wave
from pathlib import Path

from nexo_actions.admin import LISTENING_PROMPT_VERSION
from nexo_actions.agent import EchoGuard
from scripts.configure_audio import configure


class EchoGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = EchoGuard(window_seconds=8)
        self.now = 101.0
        self.spoken = [
            (
                100.0,
                "O último modelo de voz da OpenAI que atualmente roda no Codex é natural.",
            )
        ]

    def classify(self, text, **overrides):
        values = {
            "tts_active": True,
            "last_tts_end": 0.0,
            "record_during_tts": 100.5,
            "now": self.now,
        }
        values.update(overrides)
        return self.guard.classify(text, self.spoken, **values)

    def test_exact_answer_fragment_is_echo(self):
        decision = self.classify("modelo de voz da OpenAI")
        self.assertTrue(decision.is_echo)
        self.assertEqual(decision.reason, "exact_tts_fragment")

    def test_asr_corruption_of_distinctive_word_is_echo(self):
        decision = self.classify("LUCODEX")
        self.assertTrue(decision.is_echo)
        self.assertEqual(decision.reason, "distinctive_tts_word")

    def test_short_exact_tts_fragment_is_echo_only_during_overlap(self):
        spoken = [(100.0, "Claro. Tudo bem.")]
        overlapping = self.guard.classify(
            "Tudo bem",
            spoken,
            tts_active=True,
            last_tts_end=0.0,
            record_during_tts=100.5,
            now=self.now,
        )
        self.assertTrue(overlapping.is_echo)
        self.assertEqual(overlapping.reason, "short_tts_fragment")

        after_output = self.guard.classify(
            "Tudo bem",
            spoken,
            tts_active=False,
            last_tts_end=100.5,
            record_during_tts=0.0,
            now=self.now,
        )
        self.assertFalse(after_output.is_echo)

    def test_novel_human_followup_after_output_is_not_echo(self):
        decision = self.classify(
            "Quanto custa esse modelo natural?",
            tts_active=False,
            record_during_tts=0.0,
            last_tts_end=100.5,
        )
        self.assertFalse(decision.is_echo)

    def test_explicit_human_interruption_is_preserved(self):
        decision = self.classify("Nexo, para um pouco")
        self.assertFalse(decision.is_echo)
        self.assertEqual(decision.reason, "human_barge_in_cue")

    def test_single_word_interruption_is_preserved(self):
        decision = self.classify("Para")
        self.assertFalse(decision.is_echo)
        self.assertEqual(decision.reason, "human_barge_in_cue")

    def test_natural_command_during_output_is_preserved(self):
        decision = self.classify("Abre o Minecraft")
        self.assertFalse(decision.is_echo)
        self.assertEqual(decision.reason, "human_barge_in_cue")

    def test_distorted_assistant_sentence_during_output_is_quarantined(self):
        self.spoken = [
            (
                100.0,
                "A matematica estuda padroes, estruturas e quantidades usando numeros.",
            )
        ]
        decision = self.classify(
            "Essa e a instituicao de padroes estruturas e contabilidades usando numeros"
        )
        self.assertTrue(decision.is_echo)
        self.assertEqual(decision.reason, "ambiguous_tts_overlap")

    def test_ambiguous_question_during_output_requires_barge_in_cue(self):
        decision = self.classify("O que usamos para descrever isso?")
        self.assertTrue(decision.is_echo)
        self.assertEqual(decision.reason, "ambiguous_tts_overlap")

        intentional = self.classify("Nexo, o que usamos para descrever isso?")
        self.assertFalse(intentional.is_echo)
        self.assertEqual(intentional.reason, "human_barge_in_cue")

    def test_old_tts_is_not_used(self):
        decision = self.classify(
            "modelo de voz da OpenAI",
            tts_active=False,
            record_during_tts=0.0,
            last_tts_end=90.0,
        )
        self.assertFalse(decision.is_echo)
        self.assertEqual(decision.reason, "outside_tts_window")


class AudioConfigurationTests(unittest.TestCase):
    def test_configuration_is_preserving_backed_up_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mycroft.conf"
            path.write_text(
                json.dumps({"listener": {"wake_word": "nexo"}, "custom": 42}),
                encoding="utf-8",
            )
            changed, backup = configure(path, 20)
            self.assertTrue(changed)
            self.assertIsNotNone(backup)
            self.assertTrue(backup.exists())
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["custom"], 42)
            self.assertEqual(result["listener"]["wake_word"], "nexo")
            self.assertFalse(result["listener"]["fake_barge_in"])
            self.assertFalse(result["listener"]["instant_listen"])
            self.assertEqual(result["listener"]["speech_begin"], 0.30)
            self.assertEqual(result["listener"]["silence_end"], 0.90)
            self.assertEqual(result["stt"]["module"], "nexo-smart-stt")
            self.assertTrue(result["stt"]["nexo-smart-stt"]["vad_filter"])
            self.assertFalse(result["listener"]["mute_during_output"])
            self.assertEqual(result["listener"]["barge_in_volume"], 20)
            self.assertIn(
                "ovos-skill-hello-world.openvoiceos",
                result["skills"]["blacklisted_skills"],
            )

            changed_again, second_backup = configure(path, 20)
            self.assertFalse(changed_again)
            self.assertIsNone(second_backup)

    def test_open_barge_in_enables_native_listener_ducking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mycroft.conf"
            changed, _ = configure(path, 35, requires_wakeword=False)
            self.assertTrue(changed)
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(result["listener"]["fake_barge_in"])
            self.assertEqual(result["listener"]["barge_in_volume"], 35)

    def test_existing_listening_prompt_is_trimmed_and_repointed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "manda.wav"
            with wave.open(str(prompt), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x00\x00" * 1600)
                audio.writeframes((1200).to_bytes(2, "little", signed=True) * 4800)
                audio.writeframes(b"\x00\x00" * 2400)
            path = root / "mycroft.conf"
            path.write_text(json.dumps({
                "hotwords": {"nexo": {"sound": str(prompt)}},
            }), encoding="utf-8")

            changed, _ = configure(path, 100)
            self.assertTrue(changed)
            result = json.loads(path.read_text(encoding="utf-8"))
            optimized = Path(result["hotwords"]["nexo"]["sound"])
            self.assertIn(LISTENING_PROMPT_VERSION, optimized.stem)
            self.assertTrue(optimized.is_file())
            with wave.open(str(optimized), "rb") as audio:
                self.assertLess(audio.getnframes(), 6500)
            changed_again, _ = configure(path, 100)
            self.assertFalse(changed_again)


if __name__ == "__main__":
    unittest.main()
