"""Acoustic echo classification for natural full-duplex conversations."""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Sequence


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# "Sim" and "não" are intentionally absent: both can be read aloud in a
# confirmation prompt, so accepting their echo could authorize an action.
_HUMAN_INTERRUPTION_CUES = (
    "nexo para",
    "nexo pare",
    "nexo espera",
    "nexo espere",
    "para um pouco",
    "pare um pouco",
    "espera ai",
    "espere ai",
    "deixa eu falar",
    "deixe eu falar",
    "na verdade",
    "nao foi isso",
    "nao e isso",
    "muda de assunto",
)

_SINGLE_INTERRUPTION_CUES = {
    "para", "pare", "espera", "espere", "cancela", "cancele",
}

# These prefixes are deliberately conversational.  They let a person cut Nexo
# off immediately without requiring a rigid wake-word command.  A plain phrase
# such as "essa e a instituicao..." has no such evidence and is quarantined
# when its recording began while the loudspeaker was active.
_HUMAN_BARGE_IN_PREFIXES = (
    "nexo", "next", "nesso", "anexo",
    "ei", "opa", "cara", "olha", "desculpa", "com licenca",
    "mas", "eu", "quero", "preciso", "por favor",
    "me diga", "me diz", "me fala", "me explica", "me mostra",
    "abre", "abra", "fecha", "feche", "toca", "toque",
    "pesquisa", "pesquise", "procura", "procure", "mostra", "mostre",
    "aumenta", "aumente", "abaixa", "diminui", "coloca", "coloque",
    "manda", "mande", "envia", "envie", "cria", "crie",
    "reinicia", "reinicie", "desliga", "desligue",
)

_LOW_INFORMATION = {
    "a", "ao", "aos", "as", "com", "da", "das", "de", "do", "dos",
    "e", "em", "essa", "esse", "esta", "este", "eu", "foi", "isso",
    "na", "nas", "no", "nos", "o", "os", "para", "por", "que", "se",
    "sua", "seu", "um", "uma", "voce",
}


def normalize_speech(text: str) -> str:
    """Normalize accents, punctuation and whitespace without language models."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(_TOKEN_RE.findall(plain.casefold()))


@dataclass(frozen=True)
class EchoDecision:
    """Explainable result used both by the skill and its diagnostics."""

    is_echo: bool
    confidence: float
    reason: str
    similarity: float = 0.0
    token_precision: float = 0.0
    novel_tokens: tuple[str, ...] = ()


class EchoGuard:
    """Recognize fragments of recent TTS while preserving genuine barge-in.

    The classifier intentionally combines several cheap signals.  Loudspeaker
    echo often reaches STT as a clean substring, a slightly corrupted word, or
    a short window cut from a longer answer.  Human interruptions normally add
    novel content or an explicit interruption cue.
    """

    def __init__(self, *, window_seconds: float = 8.0) -> None:
        self.window_seconds = max(1.0, float(window_seconds))

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return normalize_speech(text).split()

    @staticmethod
    def _token_matches(candidate: str, spoken: str) -> bool:
        if candidate == spoken:
            return True
        if min(len(candidate), len(spoken)) < 5:
            return False
        return SequenceMatcher(None, candidate, spoken).ratio() >= 0.78

    @classmethod
    def _alignment(
        cls,
        candidate_tokens: Sequence[str],
        spoken_tokens: Sequence[str],
    ) -> tuple[float, tuple[str, ...]]:
        unmatched = []
        for candidate in candidate_tokens:
            if not any(cls._token_matches(candidate, spoken) for spoken in spoken_tokens):
                unmatched.append(candidate)
        precision = 1.0 - (len(unmatched) / max(1, len(candidate_tokens)))
        meaningful_novel = tuple(token for token in unmatched if token not in _LOW_INFORMATION)
        return precision, meaningful_novel

    @staticmethod
    def _window_similarity(candidate_tokens: Sequence[str], spoken_tokens: Sequence[str]) -> float:
        """Compare a transcript with similarly sized windows of a long answer."""
        if not candidate_tokens or not spoken_tokens:
            return 0.0
        candidate = " ".join(candidate_tokens)
        width = len(candidate_tokens)
        best = SequenceMatcher(None, candidate, " ".join(spoken_tokens)).ratio()
        for size in range(max(1, width - 1), min(len(spoken_tokens), width + 1) + 1):
            for start in range(0, len(spoken_tokens) - size + 1):
                window = " ".join(spoken_tokens[start:start + size])
                best = max(best, SequenceMatcher(None, candidate, window).ratio())
        return best

    @staticmethod
    def _has_human_barge_in_cue(normalized: str) -> bool:
        """Return whether a transcript contains intentional interruption evidence."""
        if normalized in _SINGLE_INTERRUPTION_CUES:
            return True
        if any(
            normalized == cue or normalized.startswith(cue + " ")
            for cue in _HUMAN_INTERRUPTION_CUES
        ):
            return True
        return any(
            normalized == prefix or normalized.startswith(prefix + " ")
            for prefix in _HUMAN_BARGE_IN_PREFIXES
        )

    def classify(
        self,
        utterance: str,
        recent_spoken: Iterable[tuple[float, str]],
        *,
        tts_active: bool,
        last_tts_end: float,
        record_during_tts: float = 0.0,
        now: float | None = None,
    ) -> EchoDecision:
        now = time.monotonic() if now is None else float(now)
        has_output_context = bool(tts_active)
        has_output_context = has_output_context or (
            last_tts_end > 0 and now - last_tts_end <= self.window_seconds
        )
        has_output_context = has_output_context or (
            record_during_tts > 0 and now - record_during_tts <= self.window_seconds
        )
        if not has_output_context:
            return EchoDecision(False, 0.0, "outside_tts_window")

        normalized = normalize_speech(utterance)
        candidate_tokens = normalized.split()
        if not candidate_tokens:
            return EchoDecision(False, 0.0, "empty")

        interruption_cue = self._has_human_barge_in_cue(normalized)
        overlapped_output = (
            record_during_tts > 0
            and now - record_during_tts <= self.window_seconds
        )
        best = EchoDecision(False, 0.0, "no_recent_match")
        compared_recent_output = False

        for spoken_at, spoken_text in recent_spoken:
            if now - float(spoken_at) > max(15.0, self.window_seconds):
                continue
            spoken = normalize_speech(spoken_text)
            spoken_tokens = spoken.split()
            if not spoken_tokens:
                continue
            compared_recent_output = True

            precision, novel = self._alignment(candidate_tokens, spoken_tokens)
            similarity = self._window_similarity(candidate_tokens, spoken_tokens)
            confidence = max(precision, similarity)
            if confidence > best.confidence:
                best = EchoDecision(
                    False,
                    confidence,
                    "below_threshold",
                    similarity,
                    precision,
                    novel,
                )

            # A user explicitly asking to interrupt wins unless their entire
            # multi-word phrase is an exact fragment of what Nexo just said.
            exact_fragment = normalized in spoken
            meaningful_fragment = len(candidate_tokens) >= 3 or len(normalized) >= 12
            if interruption_cue and not (exact_fragment and meaningful_fragment):
                continue

            if exact_fragment and meaningful_fragment:
                return EchoDecision(True, 0.99, "exact_tts_fragment", 1.0, precision, novel)

            # Short replies such as "Claro" and "Tudo bem" are common TTS
            # fragments but too small for the regular fuzzy thresholds.  Only
            # trust this rule when the VAD recording itself overlapped output.
            short_overlap = (
                tts_active and overlapped_output
            )
            if exact_fragment and short_overlap and not interruption_cue:
                return EchoDecision(True, 0.98, "short_tts_fragment", 1.0, precision, novel)

            if len(candidate_tokens) == 1:
                token = candidate_tokens[0]
                # Handles distinctive ASR corruptions such as "LUCODEX" for
                # "o Codex", but avoids swallowing short commands.
                if tts_active and len(token) >= 7 and precision >= 1.0:
                    return EchoDecision(True, 0.90, "distinctive_tts_word", similarity, precision, novel)
                continue

            if similarity >= 0.78 and len(normalized) >= 9 and len(novel) <= 1:
                return EchoDecision(True, similarity, "fuzzy_tts_fragment", similarity, precision, novel)

            if len(candidate_tokens) >= 3 and precision >= 0.78 and len(novel) <= 1:
                return EchoDecision(True, precision, "tts_token_alignment", similarity, precision, novel)

        if interruption_cue:
            return EchoDecision(
                False,
                best.confidence,
                "human_barge_in_cue",
                best.similarity,
                best.token_precision,
                best.novel_tokens,
            )

        # A loudspeaker fragment can be distorted so heavily by the room and
        # STT that lexical matching becomes weak.  The VAD timing is still
        # strong evidence: if the recording itself overlapped Nexo's output,
        # quarantine ambiguous speech unless the person used one of the
        # natural barge-in cues above.  This stops self-reply chains without
        # muting the microphone or delaying normal speech after Nexo finishes.
        if overlapped_output and compared_recent_output:
            return EchoDecision(
                True,
                max(0.55, best.confidence),
                "ambiguous_tts_overlap",
                best.similarity,
                best.token_precision,
                best.novel_tokens,
            )
        return best
