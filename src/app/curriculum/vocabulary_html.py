"""Parser for the external vocabulary HTML table."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

_ROW_CLASS = "RowTableOfWords_row__rUFYT"
_FIELD_CLASSES = {
    "periodicity__index": "rank",
    "periodicity__item": "frequency",
    "word__en": "term",
    "word__ts": "transcription",
    "word__translate": "translation_ru",
}
_MISSING_TRANSCRIPTION = "транскрипция не найдена"


class VocabularyParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VocabularyEntry:
    position: int
    rank: int
    term: str
    transcription: str | None
    translation_ru: str
    frequency: int


@dataclass(frozen=True, slots=True)
class VocabularyParseResult:
    entries: tuple[VocabularyEntry, ...]
    missing_ranks: tuple[int, ...]
    duplicate_terms: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.entries)

    def as_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "missing_ranks": list(self.missing_ranks),
            "duplicate_terms": list(self.duplicate_terms),
            "items": [
                {
                    "position": entry.position,
                    "frequency_rank": entry.rank,
                    "term": entry.term,
                    "transcription": entry.transcription,
                    "translation_ru": entry.translation_ru,
                    "frequency": entry.frequency,
                }
                for entry in self.entries
            ],
        }


class _VocabularyHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self._row: dict[str, str] | None = None
        self._field: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = attributes.get("class") or ""

        if tag == "li" and _ROW_CLASS in classes:
            self._row = {}
            return
        if self._row is None or tag != "p":
            return

        for class_fragment, field in _FIELD_CLASSES.items():
            if class_fragment in classes:
                self._field = field
                self._buffer = []
                return

    def handle_data(self, data: str) -> None:
        if self._field is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._field is not None and self._row is not None:
            value = "".join(self._buffer).replace("\xa0", " ").strip()
            if self._field != "translation_ru" or not self._row.get(self._field):
                self._row[self._field] = value
            self._field = None
            self._buffer = []
            return

        if tag == "li" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
            self._field = None
            self._buffer = []


def _parse_int(value: str | None, field: str, position: int) -> int:
    if value is None:
        raise VocabularyParseError(f"row {position}: missing {field}")
    digits = re.sub(r"\D", "", value)
    if not digits:
        raise VocabularyParseError(f"row {position}: invalid {field}: {value!r}")
    return int(digits)


def _parse_transcription(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or _MISSING_TRANSCRIPTION in value.casefold():
        return None
    return value


def parse_vocabulary_html(html: str) -> VocabularyParseResult:
    parser = _VocabularyHTMLParser()
    parser.feed(html)
    if not parser.rows:
        raise VocabularyParseError("no vocabulary rows found")

    entries: list[VocabularyEntry] = []
    seen_terms: dict[str, int] = {}
    duplicate_terms: set[str] = set()

    for position, row in enumerate(parser.rows, start=1):
        term = (row.get("term") or "").strip()
        translation = (row.get("translation_ru") or "").strip()
        if not term:
            raise VocabularyParseError(f"row {position}: empty term")
        if not translation:
            raise VocabularyParseError(f"row {position}: empty translation")

        entry = VocabularyEntry(
            position=position,
            rank=_parse_int(row.get("rank"), "rank", position),
            term=term,
            transcription=_parse_transcription(row.get("transcription")),
            translation_ru=translation,
            frequency=_parse_int(row.get("frequency"), "frequency", position),
        )
        entries.append(entry)

        key = term.casefold()
        if key in seen_terms:
            duplicate_terms.add(key)
        else:
            seen_terms[key] = position

    ranks = {entry.rank for entry in entries}
    max_rank = max(ranks)
    missing_ranks = tuple(rank for rank in range(1, max_rank + 1) if rank not in ranks)

    return VocabularyParseResult(
        entries=tuple(entries),
        missing_ranks=missing_ranks,
        duplicate_terms=tuple(sorted(duplicate_terms)),
    )


def load_vocabulary_html(path: Path) -> VocabularyParseResult:
    return parse_vocabulary_html(path.read_text(encoding="utf-8"))
