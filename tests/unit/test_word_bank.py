from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.curriculum.errors import ContentLoadError
from app.curriculum.models import VocabularyPayload
from app.curriculum.word_bank import (
    WordBankFile,
    compile_word_bank,
    load_word_bank_path,
)


def write_bank(path: Path, words: list[dict[str, object]], **extra: object) -> None:
    document = {"source": "test", "license": "unknown", "words": words, **extra}
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")


def word(
    position: int,
    term: str,
    translation: str,
    *,
    frequency: int = 1000,
    **extra: object,
) -> dict[str, object]:
    return {
        "position": position,
        "frequency": frequency,
        "term": term,
        "translation_ru": translation,
        **extra,
    }


def test_compiles_minimal_bank_into_content_file() -> None:
    bank = WordBankFile.model_validate(
        {
            "source": "3000_words.html",
            "license": "unknown",
            "words": [
                word(1, "be", "быть", transcription="biː"),
                word(2, "and", "и"),
            ],
        }
    )

    content = compile_word_bank(bank, topic_slug="core-words-0001-0002")

    assert content.topic.slug == "core-words-0001-0002"
    assert [item.slug for item in content.items] == ["word-be", "word-and"]

    payload = content.items[0].payload
    assert isinstance(payload, VocabularyPayload)
    assert payload.term == "be"
    assert payload.translations_ru == ["быть"]
    assert payload.frequency == 1000
    assert payload.frequency_rank == 1
    assert payload.transcription == "biː"


def test_accepted_ru_is_carried_into_payload() -> None:
    bank = WordBankFile.model_validate(
        {
            "words": [
                word(
                    1,
                    "can",
                    "Мочь, уметь",
                    accepted_ru=["мочь", "уметь"],
                )
            ]
        }
    )

    content = compile_word_bank(bank, topic_slug="core-words-0001-0001")
    payload = content.items[0].payload

    assert isinstance(payload, VocabularyPayload)
    assert payload.translations_ru == ["Мочь, уметь"]
    assert payload.accepted_ru == ["мочь", "уметь"]


def test_slug_collisions_get_a_deterministic_suffix() -> None:
    bank = WordBankFile.model_validate(
        {
            "words": [
                word(1, "good bye", "до свидания"),
                word(2, "good-bye", "пока"),
            ]
        }
    )

    content = compile_word_bank(bank, topic_slug="core-words-0001-0002")

    assert [item.slug for item in content.items] == ["word-good-bye", "word-good-bye-2"]


def test_duplicate_terms_are_rejected() -> None:
    with pytest.raises(ValidationError, match="unique"):
        WordBankFile.model_validate(
            {
                "words": [
                    word(1, "be", "быть"),
                    word(2, "BE", "существовать"),
                ]
            }
        )


def test_duplicate_positions_are_rejected() -> None:
    with pytest.raises(ValidationError, match="positions"):
        WordBankFile.model_validate(
            {
                "words": [
                    word(1, "be", "быть"),
                    word(1, "and", "и"),
                ]
            }
        )


def test_load_word_bank_path_compiles_each_file_and_checks_global_terms(
    tmp_path: Path,
) -> None:
    write_bank(
        tmp_path / "core_words_0001_0002.yaml",
        [word(1, "be", "быть"), word(2, "and", "и")],
    )
    write_bank(
        tmp_path / "core_words_0003_0004.yaml",
        [word(3, "go", "идти")],
    )

    loaded = load_word_bank_path(tmp_path)

    assert [content.topic.slug for _, content in loaded] == [
        "core-words-0001-0002",
        "core-words-0003-0004",
    ]
    assert [len(content.items) for _, content in loaded] == [2, 1]


def test_load_word_bank_path_rejects_duplicate_terms_across_files(
    tmp_path: Path,
) -> None:
    write_bank(tmp_path / "core_words_0001_0001.yaml", [word(1, "be", "быть")])
    write_bank(tmp_path / "core_words_0002_0002.yaml", [word(2, "BE", "существовать")])

    with pytest.raises(ContentLoadError, match="duplicate word"):
        load_word_bank_path(tmp_path)
