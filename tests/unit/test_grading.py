from __future__ import annotations

from pathlib import Path

import pytest

from app.curriculum.loader import load_content_file
from app.curriculum.models import (
    ContentItemSpec,
    GapFillPayload,
    IrregularVerbPayload,
    MultipleChoicePayload,
    TranslationPayload,
    WordOrderPayload,
)
from app.db.enums import CEFRLevel, Checker, Verdict
from app.grading.distance import edit_distance
from app.grading.grader import (
    accepted_answers,
    grade_answer,
    grade_item,
    grade_multiple_choice,
)
from app.grading.normalize import normalize_answer

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_item(payload: object, *, slug: str = "test-item") -> ContentItemSpec:
    return ContentItemSpec.model_validate(
        {
            "slug": slug,
            "level": CEFRLevel.A2,
            "payload": payload,
        }
    )


def test_normalize_case_punctuation_and_whitespace() -> None:
    assert normalize_answer("  I   went, to the cinema! ") == "i went to the cinema"
    assert normalize_answer("«Hello» — world…") == "hello - world"


def test_normalize_apostrophes_and_contractions() -> None:
    assert normalize_answer("I don’t know") == "i do not know"
    assert normalize_answer("CAN’T") == "can not"
    assert normalize_answer("cannot") == "can not"
    assert normalize_answer("won't") == "will not"
    assert normalize_answer("she isn't here") == "she is not here"
    assert normalize_answer("I'm here") == "i am here"
    assert normalize_answer("we're ready") == "we are ready"
    assert normalize_answer("I'll go") == "i will go"
    assert normalize_answer("they've gone") == "they have gone"


def test_normalize_applies_nfkc() -> None:
    assert normalize_answer("ＧＯ") == "go"
    assert normalize_answer("café") == "café"


def test_edit_distance_handles_transposition() -> None:
    assert edit_distance("wnet", "went") == 1
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("", "go") == 2


def test_exact_match_accepts_variants() -> None:
    result = grade_answer("WENT.", ["went", "gone"])

    assert result.verdict is Verdict.CORRECT
    assert result.matched == "went"
    assert result.expected == "went / gone"
    assert result.is_correct
    assert result.checker is Checker.CODE


def test_fuzzy_marks_single_typo_as_partial() -> None:
    result = grade_answer("wnet", ["went"])

    assert result.verdict is Verdict.PARTIAL
    assert result.is_typo
    assert result.distance == 1
    assert result.matched == "went"


def test_fuzzy_allows_one_typo_in_a_phrase() -> None:
    result = grade_answer("I have live here", ["I have lived here"])

    assert result.verdict is Verdict.PARTIAL
    assert result.distance == 1


def test_fuzzy_rejects_multiple_typos() -> None:
    result = grade_answer("I hav livd here", ["I have lived here"])

    assert result.verdict is Verdict.INCORRECT


def test_fuzzy_rejects_missing_word() -> None:
    result = grade_answer("I went cinema", ["I went to the cinema"])

    assert result.verdict is Verdict.INCORRECT


def test_fuzzy_can_be_disabled() -> None:
    result = grade_answer("wnet", ["went"], fuzzy=False)

    assert result.verdict is Verdict.INCORRECT


def test_empty_answer_is_incorrect() -> None:
    result = grade_answer("", ["went"])

    assert result.verdict is Verdict.INCORRECT
    assert result.normalized_answer == ""


def test_empty_accepted_rejected() -> None:
    with pytest.raises(ValueError, match="accepted"):
        grade_answer("went", [])
    with pytest.raises(ValueError, match="accepted"):
        grade_answer("went", ["  "])


def test_duplicate_variants_are_collapsed() -> None:
    result = grade_answer("went", ["went", "WENT.", "gone"])

    assert result.expected == "went / gone"


def test_irregular_verb_forms_and_variants() -> None:
    item = make_item(
        IrregularVerbPayload(
            base="be",
            past=["was", "were"],
            participle=["been"],
            translation_ru="быть",
        )
    )

    assert accepted_answers(item, form="past") == ("was", "were")
    assert grade_item(item, "were", form="past").verdict is Verdict.CORRECT
    assert grade_item(item, "been", form="participle").verdict is Verdict.CORRECT
    assert grade_item(item, "went", form="past").verdict is Verdict.INCORRECT


def test_gap_fill_uses_answer_and_accepted() -> None:
    item = make_item(
        GapFillPayload(
            sentence="She ___ to school.",
            answer="goes",
            accepted=["goes"],
        )
    )

    assert grade_item(item, " Goes. ").verdict is Verdict.CORRECT


def test_translation_uses_reference_and_accepted() -> None:
    item = make_item(
        TranslationPayload(
            prompt_ru="Я вчера ходил в кино.",
            reference="I went to the cinema yesterday.",
            accepted=["Yesterday I went to the cinema"],
        )
    )

    assert grade_item(item, "yesterday i went to the cinema").verdict is Verdict.CORRECT


def test_word_order_uses_expected_sentence() -> None:
    item = make_item(
        WordOrderPayload(
            words=["I", "went", "to", "the", "cinema"],
            answer="I went to the cinema",
        )
    )

    assert grade_item(item, "I went to the cinema.").verdict is Verdict.CORRECT
    assert grade_item(item, "I went cinema").verdict is Verdict.INCORRECT


def test_multiple_choice_by_text_and_index() -> None:
    payload = MultipleChoicePayload(
        question="I ___ here since 2020.",
        options=["live", "lived", "have lived"],
        correct_index=2,
    )
    item = make_item(payload)

    assert grade_item(item, "Have lived.").verdict is Verdict.CORRECT
    assert grade_multiple_choice(payload, selected_index=2).verdict is Verdict.CORRECT
    assert grade_multiple_choice(payload, selected_index=0).verdict is Verdict.INCORRECT

    with pytest.raises(ValueError, match="selected_index"):
        grade_multiple_choice(payload)


def test_real_irregular_verb_bank_grades() -> None:
    content = load_content_file(PROJECT_ROOT / "content" / "topics" / "irregular_verbs.yaml")
    go = next(
        item
        for item in content.items
        if isinstance(item.payload, IrregularVerbPayload) and item.payload.base == "go"
    )

    assert grade_item(go, "went", form="past").verdict is Verdict.CORRECT
    assert grade_item(go, "gone", form="participle").verdict is Verdict.CORRECT
    assert grade_item(go, "wnt", form="past").verdict is Verdict.PARTIAL
