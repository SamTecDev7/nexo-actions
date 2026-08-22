import unittest
from types import SimpleNamespace

from nexo_actions.stt_plugin import NexoSmartSTT
from nexo_actions.stt_quality import (
    SMART_STT_MODULE,
    active_stt_settings,
    assess_speech_quality,
    configure_smart_stt,
)


class SpeechQualityTests(unittest.TestCase):
    def test_subsecond_capture_is_rejected_without_inspecting_text(self):
        quality = assess_speech_quality(
            audio_seconds=0.78,
            speech_seconds=0.52,
            average_log_probability=-0.20,
            no_speech_probability=0.05,
            compression_ratio=1.0,
        )
        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "capture_too_short")

    def test_short_real_command_with_sustained_voice_is_preserved(self):
        quality = assess_speech_quality(
            audio_seconds=1.25,
            speech_seconds=0.42,
            average_log_probability=-0.30,
            no_speech_probability=0.08,
            compression_ratio=1.1,
        )
        self.assertTrue(quality.accepted)
        self.assertGreater(quality.confidence, 0.7)

    def test_silence_is_rejected_even_when_decoder_returns_tokens(self):
        quality = assess_speech_quality(
            audio_seconds=2.1,
            speech_seconds=0.12,
            average_log_probability=-0.40,
            no_speech_probability=0.88,
            compression_ratio=1.0,
        )
        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "insufficient_speech")

    def test_migration_keeps_model_and_enables_signal_filters(self):
        root = {
            "module": "ovos-stt-plugin-fasterwhisper",
            "ovos-stt-plugin-fasterwhisper": {
                "model": "large-v3",
                "use_cuda": True,
                "beam_size": 5,
            },
        }
        configured = configure_smart_stt(root)
        self.assertEqual(root["module"], SMART_STT_MODULE)
        self.assertEqual(configured["model"], "large-v3")
        self.assertTrue(configured["vad_filter"])
        self.assertIn("quality_gate", configured)
        self.assertEqual(active_stt_settings({"stt": root})["model"], "large-v3")


class SmartSTTPluginTests(unittest.TestCase):
    class Audio:
        def __init__(self, seconds):
            self.payload = b"\x10\x00" * int(16_000 * seconds)

        def get_raw_data(self, convert_rate=None, convert_width=None):
            if convert_rate != 16_000 or convert_width != 2:
                raise AssertionError("Nexo must normalize audio for Whisper")
            return self.payload

    class Engine:
        def __init__(self, segment, duration_after_vad):
            self.segment = segment
            self.duration_after_vad = duration_after_vad
            self.options = None

        def transcribe(self, _samples, **options):
            self.options = options
            return iter([self.segment]), SimpleNamespace(
                duration_after_vad=self.duration_after_vad
            )

    @staticmethod
    def plugin(engine):
        plugin = NexoSmartSTT.__new__(NexoSmartSTT)
        plugin.lang = "pt-BR"
        plugin.beam_size = 5
        plugin.config = {}
        plugin.engine = engine
        return plugin

    def test_plugin_returns_real_confidence_and_forces_vad(self):
        segment = SimpleNamespace(
            text=" Pare.", start=0.0, end=0.42, avg_logprob=-0.25,
            no_speech_prob=0.04, compression_ratio=1.1,
        )
        engine = self.Engine(segment, 0.42)
        result = self.plugin(engine).transcribe(self.Audio(1.25))
        self.assertEqual(result[0][0], "Pare.")
        self.assertGreater(result[0][1], 0.7)
        self.assertLess(result[0][1], 1.0)
        self.assertTrue(engine.options["vad_filter"])
        self.assertFalse(engine.options["condition_on_previous_text"])

    def test_plugin_drops_confident_words_from_broken_short_capture(self):
        segment = SimpleNamespace(
            text=" Obrigado.", start=0.0, end=0.50, avg_logprob=-0.15,
            no_speech_prob=0.03, compression_ratio=1.0,
        )
        engine = self.Engine(segment, 0.50)
        result = self.plugin(engine).transcribe(self.Audio(0.72))
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
