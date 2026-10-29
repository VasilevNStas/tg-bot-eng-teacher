from __future__ import annotations

import pytest

from app.curriculum.vocabulary_clean import (
    RawVocabularyEntry,
    VocabularyCleanError,
    normalize_translation,
    normalize_vocabulary,
    parse_numbered_vocabulary_text,
)
from app.curriculum.vocabulary_html import VocabularyEntry, VocabularyParseResult


def make_html_result(*entries: VocabularyEntry) -> VocabularyParseResult:
    return VocabularyParseResult(
        entries=entries,
        missing_ranks=(),
        duplicate_terms=(),
    )


def test_normalize_translation_splits_variants_and_extracts_notes() -> None:
    translations, notes = normalize_translation(
        'забота / беспокойство ("I don\'t care" - мне всё равно)'
    )

    assert translations == ["забота", "беспокойство"]
    assert notes == '"I don\'t care" - мне всё равно'


def test_normalize_translation_deduplicates_case_insensitively() -> None:
    translations, notes = normalize_translation("оставить, Оставить, забросить")

    assert translations == ["оставить", "забросить"]
    assert notes is None


def test_parse_numbered_vocabulary_text() -> None:
    text = """
    1
    abandon
    оставить, забросить
    2
    abate
    уменьшаться
    """

    entries = parse_numbered_vocabulary_text(text)

    assert entries == (
        RawVocabularyEntry(term="abandon", translation_ru="оставить, забросить"),
        RawVocabularyEntry(term="abate", translation_ru="уменьшаться"),
    )


def test_parse_numbered_vocabulary_rejects_bad_shape() -> None:
    with pytest.raises(VocabularyCleanError, match="triples"):
        parse_numbered_vocabulary_text("1\nabandon\n")


def test_normalize_vocabulary_merges_sources_and_preserves_frequency() -> None:
    html = make_html_result(
        VocabularyEntry(
            position=1,
            rank=1,
            term="abandon",
            transcription="əˈbændən",
            translation_ru="оставить, забросить",
            frequency=914627,
        ),
        VocabularyEntry(
            position=2,
            rank=2,
            term="the",
            transcription="ðiː",
            translation_ru="Определенный артикль",
            frequency=864743,
        ),
    )
    rtf = (
        RawVocabularyEntry(term="abandon", translation_ru="покинуть"),
        RawVocabularyEntry(term="custom", translation_ru="обычай"),
    )

    result = normalize_vocabulary(html, rtf)

    assert result.unique_terms == 3
    assert result.merged_terms == 1
    abandon = next(entry for entry in result.entries if entry.term == "abandon")
    assert abandon.translations_ru == ("оставить", "забросить", "покинуть")
    assert abandon.frequency == 914627
    assert abandon.frequency_rank == 1
    assert abandon.transcription == "əˈbændən"
    assert abandon.sources == ("html", "rtf")

    the = next(entry for entry in result.entries if entry.term == "the")
    assert the.function_word is True

    custom = next(entry for entry in result.entries if entry.term == "custom")
    assert custom.frequency is None
    assert custom.frequency_rank is None
    assert custom.sources == ("rtf",)
    assert result.function_words == 1
