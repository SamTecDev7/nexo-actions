import tempfile
import unittest
from pathlib import Path

from nexo_actions.neural_tts_server import (
    PROFILES,
    NeuralRuntime,
    _chunks,
    _clean_for_speech,
    _join_audio_segments,
)


class NeuralTTSServerContractTests(unittest.TestCase):
    def test_text_cleanup_removes_markdown_and_urls(self):
        cleaned = _clean_for_speech("A **Clube Envios** está [aqui](https://example.com).")
        self.assertEqual(cleaned, "A Clube Envios está aqui.")

    def test_long_answers_are_split_on_sentence_boundaries(self):
        parts = _chunks("Primeira frase curta. " + "x" * 320 + " Final.", maximum=120)
        self.assertGreater(len(parts), 2)
        self.assertTrue(all(len(part) <= 120 for part in parts))

    def test_normal_words_are_never_cut_between_chunks(self):
        text = " ".join(f"palavra{index}" for index in range(80))
        parts = _chunks(text, maximum=90)
        self.assertEqual(" ".join(parts), text)
        self.assertTrue(all(len(part) <= 90 for part in parts))

    def test_audio_segments_are_crossfaded_instead_of_hard_joined(self):
        import numpy as np

        left = np.ones(240, dtype=np.float32)
        right = -np.ones(240, dtype=np.float32)
        joined = _join_audio_segments([left, right], 24_000, crossfade_ms=5)
        self.assertLess(len(joined), len(left) + len(right))
        self.assertLess(float(np.max(np.abs(np.diff(joined)))), 0.1)

    def test_runtime_requires_every_verified_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = NeuralRuntime(root)
            self.assertFalse(runtime.installed)
            required = (
                root / "installed.json",
                root / "assembled" / "ve.pt",
                root / "assembled" / "conds.pt",
                root / "assembled" / "t3_pt_br.safetensors",
                root / "assembled" / "s3gen_v3.pt",
                root / "assembled" / "grapheme_mtl_merged_expanded_v1.json",
                root / "prompts" / "pt_br_f2.wav",
                root / "kokoro" / "config.json",
                root / "kokoro" / "kokoro-v1_0.pth",
                root / "kokoro" / "voices" / "pf_dora.pt",
                root / "kokoro" / "voices" / "pm_alex.pt",
                root / "kokoro" / "voices" / "pm_santa.pt",
            )
            for path in required:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"ok")
            self.assertTrue(runtime.installed)

    def test_default_profiles_are_low_latency_and_studio_is_explicit(self):
        self.assertEqual(PROFILES["neural:ptbr-natural"]["engine"], "kokoro")
        self.assertEqual(PROFILES["neural:ptbr-calm"]["engine"], "kokoro")
        self.assertEqual(PROFILES["neural:ptbr-expressive"]["engine"], "kokoro")
        self.assertEqual(PROFILES["neural:ptbr-studio"]["engine"], "chatterbox")


if __name__ == "__main__":
    unittest.main()
