"""Typed golden-eval cases and level-aware reply limits."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.db.enums import CEFRLevel, ConversationMode, ErrorCategory

SUITES = ("chat", "correction", "roleplay")

LEVEL_REPLY_WORD_CAPS: dict[CEFRLevel, int] = {
    CEFRLevel.A1: 40,
    CEFRLevel.A2: 60,
    CEFRLevel.B1: 90,
    CEFRLevel.B2: 120,
    CEFRLevel.C1: 150,
    CEFRLevel.C2: 180,
}

DEFAULT_FORBIDDEN_PHRASES = (
    "as an ai",
    "system prompt",
    "language model",
)


@dataclass(frozen=True, slots=True)
class ExpectedCorrection:
    """One correction that must be present in a structured response."""

    original: str
    corrected: str
    category: ErrorCategory | None = None


@dataclass(frozen=True, slots=True)
class EvalExpectation:
    """Deterministic rubric properties for one golden case."""

    reply_language: str = "en"
    min_reply_words: int = 1
    max_reply_words: int = 80
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = DEFAULT_FORBIDDEN_PHRASES
    corrections: tuple[ExpectedCorrection, ...] = ()
    max_corrections: int = 3
    require_russian_explanations: bool = True
    vocabulary: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One golden dialogue turn with a replay fixture and rubric."""

    id: str
    suite: str
    level: CEFRLevel
    input: str
    scenario: str | None
    replay: Mapping[str, object] | str
    expectation: EvalExpectation

    @property
    def mode(self) -> ConversationMode:
        return ConversationMode(self.suite)

    @property
    def structured(self) -> bool:
        return self.suite in {"chat", "correction"}
