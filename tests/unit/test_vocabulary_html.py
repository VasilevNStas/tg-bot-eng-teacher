from __future__ import annotations

import pytest

from app.curriculum.vocabulary_html import (
    VocabularyParseError,
    parse_vocabulary_html,
)

ROW = """
<li class="RowTableOfWords_row__rUFYT">
  <div class="RowTableOfWords_row__column__M1B8f RowTableOfWords_periodicity__AOtPo">
    <p class="RowTableOfWords_periodicity__index__YP3LS">{rank})&nbsp;</p>
    <p class="RowTableOfWords_periodicity__item__HFzFa">{frequency} раз </p>
  </div>
  <button class="RowTableOfWords_row__column__M1B8f RowTableOfWords_word__GQp1A">
    <div>
      <p class="RowTableOfWords_word__en__2WPvN">{term}</p>
      <p class="RowTableOfWords_word__ts__aHGWr">{transcription}</p>
      <p class="RowTableOfWords_word__translate__vkpSw">{translation}</p>
    </div>
  </button>
  <p class="RowTableOfWords_translate__3S0qT RowTableOfWords_row__column__M1B8f">{translation}</p>
</li>
"""


def make_row(
    rank: int,
    frequency: int,
    term: str,
    transcription: str,
    translation: str,
) -> str:
    return ROW.format(
        rank=rank,
        frequency=frequency,
        term=term,
        transcription=transcription,
        translation=translation,
    )


def test_parse_rows_preserves_frequency_rank_and_transcription() -> None:
    html = (
        "<ul>"
        + make_row(1, 914627, "be", "biː", "быть")
        + make_row(3, 515, "haunt", "hɔːnt", "преследовать")
        + "</ul>"
    )

    result = parse_vocabulary_html(html)

    assert result.count == 2
    assert result.entries[0].position == 1
    assert result.entries[0].rank == 1
    assert result.entries[0].frequency == 914627
    assert result.entries[0].term == "be"
    assert result.entries[0].transcription == "biː"
    assert result.entries[0].translation_ru == "быть"
    assert result.missing_ranks == (2,)
    assert result.duplicate_terms == ()


def test_parse_missing_transcription_becomes_none() -> None:
    html = make_row(1, 10, "word", "транскрипция не найдена!", "слово")

    result = parse_vocabulary_html(html)

    assert result.entries[0].transcription is None


def test_parse_reports_duplicate_terms() -> None:
    html = make_row(1, 100, "be", "biː", "быть") + make_row(2, 90, "Be", "biː", "быть")

    result = parse_vocabulary_html(html)

    assert result.count == 2
    assert result.duplicate_terms == ("be",)
    assert result.missing_ranks == ()


def test_parse_rejects_empty_term() -> None:
    html = make_row(1, 10, " ", "biː", "быть")

    with pytest.raises(VocabularyParseError, match="empty term"):
        parse_vocabulary_html(html)


def test_parse_rejects_empty_document() -> None:
    with pytest.raises(VocabularyParseError, match="no vocabulary rows"):
        parse_vocabulary_html("<ul></ul>")
