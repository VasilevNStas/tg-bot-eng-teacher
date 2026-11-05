from __future__ import annotations

from app.db.enums import ErrorCategory
from app.services.correction.schema import (
    CorrectionItem,
    CorrectionPayload,
    NewVocabularyItem,
)
from app.services.correction.validation import (
    MAX_CORRECTIONS,
    MAX_EXPLANATION_CHARS,
    MAX_REPLY_CHARS,
    MAX_VOCABULARY,
    sanitize_payload,
)


def make_payload(
    *,
    reply: str = "Nice!",
    corrections: list[CorrectionItem] | None = None,
    vocabulary: list[NewVocabularyItem] | None = None,
) -> CorrectionPayload:
    return CorrectionPayload(
        reply=reply,
        corrections=corrections or [],
        new_vocabulary=vocabulary or [],
    )


def correction(original: str, *, category: ErrorCategory = ErrorCategory.TENSE) -> CorrectionItem:
    return CorrectionItem(
        original=original,
        corrected=original.title(),
        category=category,
        explanation_ru="Короткое объяснение.",
    )


def test_sanitize_drops_corrections_not_present_in_user_text() -> None:
    payload = make_payload(corrections=[correction("I go"), correction("invented")])

    result = sanitize_payload(payload, "I go to the cinema")

    assert [item.original for item in result.corrections] == ["I go"]


def test_sanitize_is_case_and_whitespace_insensitive() -> None:
    payload = make_payload(corrections=[correction("i   GO")])

    result = sanitize_payload(payload, "Well, I Go there")

    assert len(result.corrections) == 1


def test_sanitize_caps_lists_and_free_text() -> None:
    corrections = [correction(f"word{i}") for i in range(MAX_CORRECTIONS + 3)]
    vocabulary = [
        NewVocabularyItem(term=f"term{i}", translation_ru="перевод", context="context")
        for i in range(MAX_VOCABULARY + 2)
    ]
    payload = make_payload(
        reply="x" * (MAX_REPLY_CHARS + 50),
        corrections=corrections,
        vocabulary=vocabulary,
    )
    user_text = " ".join(f"word{i}" for i in range(len(corrections)))

    result = sanitize_payload(payload, user_text)

    assert len(result.corrections) == MAX_CORRECTIONS
    assert len(result.new_vocabulary) == MAX_VOCABULARY
    assert len(result.reply) == MAX_REPLY_CHARS


def test_sanitize_truncates_explanation() -> None:
    item = CorrectionItem(
        original="I go",
        corrected="I went",
        category=ErrorCategory.TENSE,
        explanation_ru="x" * (MAX_EXPLANATION_CHARS + 20),
    )

    result = sanitize_payload(make_payload(corrections=[item]), "I go home")

    assert len(result.corrections[0].explanation_ru) == MAX_EXPLANATION_CHARS
