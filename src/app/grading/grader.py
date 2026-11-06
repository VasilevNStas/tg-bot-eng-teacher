"""Deterministic grading ladder: exact → variants → fuzzy (ADR-016)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.curriculum.models import (
    ContentItemSpec,
    GapFillPayload,
    IrregularVerbPayload,
    MultipleChoicePayload,
    TranslationPayload,
    VocabularyPayload,
    WordOrderPayload,
)
from app.db.enums import Checker, Verdict
from app.grading.distance import edit_distance
from app.grading.normalize import normalize_answer

IrregularForm = Literal["past", "participle"]


@dataclass(frozen=True, slots=True)
class GradeResult:
    """Outcome of comparing one user answer with the accepted variants.

    ``expected`` is a human-readable list of accepted answers, ``matched`` is
    the variant that actually matched, and ``distance`` is the edit distance
    to the closest variant (useful for diagnostics and "almost correct" UX).
    """

    verdict: Verdict
    expected: str
    normalized_answer: str
    matched: str | None = None
    distance: int = 0
    is_typo: bool = False
    checker: Checker = Checker.CODE
    explanation: str | None = None

    @property
    def is_correct(self) -> bool:
        return self.verdict is Verdict.CORRECT


def grade_answer(
    answer: str,
    accepted: Sequence[str],
    *,
    fuzzy: bool = True,
) -> GradeResult:
    """Grade ``answer`` against ``accepted`` using the exact → fuzzy ladder.

    Exact matches are normalized first (case, punctuation, contractions), so
    ``Don't`` and ``do not`` are interchangeable. If no exact variant matches
    and ``fuzzy`` is enabled, a single small typo yields ``PARTIAL`` instead
    of ``INCORRECT``; the caller decides whether that counts as correct.
    """
    pairs = _normalized_variants(accepted)
    expected = " / ".join(raw for _, raw in pairs)
    normalized = normalize_answer(answer)

    # An empty answer can never be correct; still report the closest variant's
    # length so the UI can say "you are N characters away".
    if not normalized:
        return GradeResult(
            verdict=Verdict.INCORRECT,
            expected=expected,
            normalized_answer=normalized,
            distance=min(len(norm) for norm, _ in pairs),
        )

    # Exact pass: compare only normalized forms, but keep the raw variant for
    # the ``matched`` field that is shown back to the user.
    for norm, raw in pairs:
        if normalized == norm:
            return GradeResult(
                verdict=Verdict.CORRECT,
                expected=expected,
                normalized_answer=normalized,
                matched=raw,
            )

    if fuzzy:
        best: tuple[int, str] | None = None
        for norm, raw in pairs:
            score = _fuzzy_score(normalized, norm)
            if score is not None and (best is None or score < best[0]):
                best = (score, raw)
        if best is not None:
            return GradeResult(
                verdict=Verdict.PARTIAL,
                expected=expected,
                normalized_answer=normalized,
                matched=best[1],
                distance=best[0],
                is_typo=True,
            )

    return GradeResult(
        verdict=Verdict.INCORRECT,
        expected=expected,
        normalized_answer=normalized,
        distance=min(edit_distance(normalized, norm) for norm, _ in pairs),
    )


def accepted_answers(
    item: ContentItemSpec,
    *,
    form: IrregularForm = "past",
) -> tuple[str, ...]:
    """Return every answer that should be accepted for ``item``.

    Each payload kind stores its reference answer differently (``answer``,
    ``reference``, ``options[correct_index]``, ...). This is the single place
    that translates a payload into a flat list of accepted strings, so the
    grading ladder and callers do not need per-kind branching.
    """
    payload = item.payload

    if isinstance(payload, IrregularVerbPayload):
        # Regular verbs use ``past`` by default; the drill can ask for the
        # participle instead.
        if form == "past":
            return tuple(payload.past)
        if form == "participle":
            return tuple(payload.participle)
        raise ValueError(f"unknown irregular verb form: {form}")

    if isinstance(payload, GapFillPayload):
        return (payload.answer, *payload.accepted)

    if isinstance(payload, TranslationPayload):
        return (payload.reference, *payload.accepted)

    if isinstance(payload, WordOrderPayload):
        return (payload.answer,)

    if isinstance(payload, MultipleChoicePayload):
        return (payload.options[payload.correct_index],)

    if isinstance(payload, VocabularyPayload):
        return (payload.term, *payload.accepted_en)

    raise TypeError(f"unsupported content payload: {type(payload)!r}")


def grade_item(
    item: ContentItemSpec,
    answer: str,
    *,
    form: IrregularForm = "past",
    fuzzy: bool = True,
) -> GradeResult:
    """Grade a free-text answer for any content item.

    Multiple-choice items do not go through the fuzzy ladder: selection is
    exact by design. Everything else uses ``grade_answer`` with the accepted
    variants extracted by ``accepted_answers``.
    """
    if isinstance(item.payload, MultipleChoicePayload):
        return grade_multiple_choice(item.payload, answer=answer)
    return grade_answer(answer, accepted_answers(item, form=form), fuzzy=fuzzy)


def grade_multiple_choice(
    payload: MultipleChoicePayload,
    *,
    answer: str | None = None,
    selected_index: int | None = None,
) -> GradeResult:
    """Grade a multiple-choice answer either by index or by option text.

    Index-based grading is used by inline keyboards (no parsing ambiguity);
    text-based grading exists for tests and text answers.
    """
    expected = payload.options[payload.correct_index]

    if selected_index is not None:
        correct = selected_index == payload.correct_index
        return GradeResult(
            verdict=Verdict.CORRECT if correct else Verdict.INCORRECT,
            expected=expected,
            normalized_answer=f"index:{selected_index}",
            matched=expected if correct else None,
        )

    if answer is None:
        raise ValueError("answer or selected_index is required")

    normalized = normalize_answer(answer)
    expected_normalized = normalize_answer(expected)
    if normalized == expected_normalized:
        return GradeResult(
            verdict=Verdict.CORRECT,
            expected=expected,
            normalized_answer=normalized,
            matched=expected,
        )

    return GradeResult(
        verdict=Verdict.INCORRECT,
        expected=expected,
        normalized_answer=normalized,
        distance=edit_distance(normalized, expected_normalized),
    )


def _normalized_variants(accepted: Sequence[str]) -> list[tuple[str, str]]:
    """Normalize, deduplicate and pair each variant with its raw form.

    Duplicate variants can appear after normalization (``don't`` vs ``do not``)
    or in hand-written YAML; grading should not care about them.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in accepted:
        normalized = normalize_answer(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        pairs.append((normalized, raw))
    if not pairs:
        raise ValueError("accepted answers must contain at least one non-empty value")
    return pairs


def _fuzzy_score(answer: str, target: str) -> int | None:
    """Return the typo cost if ``answer`` is one small edit away, else ``None``.

    The rules are intentionally conservative:

    - token counts must match, so missing/extra words are not "typos";
    - only one token may differ;
    - the allowed distance scales with length (1 for short words, 2 for
      longer ones), which avoids turning ``cat`` into ``cut`` silently.
    """
    answer_tokens = answer.split()
    target_tokens = target.split()
    if not answer_tokens or len(answer_tokens) != len(target_tokens):
        return None

    changed = 0
    total = 0
    for answer_token, target_token in zip(answer_tokens, target_tokens, strict=True):
        distance = edit_distance(answer_token, target_token)
        if distance == 0:
            continue
        limit = 1 if len(target_token) <= 4 else 2
        if distance > limit:
            return None
        changed += 1
        total += distance

    # More than one changed token is not a typo, it is a different answer.
    return total if changed == 1 else None
