"""Normalize and merge vocabulary source lists (B-062)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.curriculum.vocabulary_html import VocabularyEntry, VocabularyParseResult

_PARENTHESES = re.compile(r"\(([^()]*)\)")
_SEPARATORS = re.compile(r"\s*[/,;]\s*")
_TRIM = " \t\r\n.,;:/!?'"
_MAX_TRANSLATIONS = 20
_MAX_NOTES = 512


class VocabularyCleanError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RawVocabularyEntry:
    term: str
    translation_ru: str


@dataclass(frozen=True, slots=True)
class NormalizedVocabularyEntry:
    position: int
    term: str
    translations_ru: tuple[str, ...]
    transcription: str | None
    frequency: int | None
    frequency_rank: int | None
    notes_ru: str | None
    sources: tuple[str, ...]
    function_word: bool


@dataclass(slots=True)
class _MutableVocabularyEntry:
    term: str
    translations_ru: list[str]
    transcription: str | None
    frequency: int | None
    frequency_rank: int | None
    notes_ru: str | None
    sources: set[str]


@dataclass(frozen=True, slots=True)
class NormalizedVocabularyResult:
    entries: tuple[NormalizedVocabularyEntry, ...]
    html_count: int
    rtf_count: int
    merged_terms: int
    html_missing_ranks: tuple[int, ...]
    html_duplicate_terms: tuple[str, ...]

    @property
    def unique_terms(self) -> int:
        return len(self.entries)

    @property
    def function_words(self) -> int:
        return sum(entry.function_word for entry in self.entries)

    def as_dict(self) -> dict[str, object]:
        return {
            "html_count": self.html_count,
            "rtf_count": self.rtf_count,
            "unique_terms": self.unique_terms,
            "merged_terms": self.merged_terms,
            "html_missing_ranks": list(self.html_missing_ranks),
            "html_duplicate_terms": list(self.html_duplicate_terms),
            "function_words": self.function_words,
            "stoplist": sorted(FUNCTION_WORDS),
            "items": [
                {
                    "position": entry.position,
                    "term": entry.term,
                    "translations_ru": list(entry.translations_ru),
                    "transcription": entry.transcription,
                    "frequency": entry.frequency,
                    "frequency_rank": entry.frequency_rank,
                    "notes_ru": entry.notes_ru,
                    "sources": list(entry.sources),
                    "function_word": entry.function_word,
                }
                for entry in self.entries
            ],
        }


FUNCTION_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "because",
        "as",
        "than",
        "so",
        "that",
        "this",
        "these",
        "those",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "its",
        "our",
        "their",
        "mine",
        "yours",
        "hers",
        "ours",
        "theirs",
        "am",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "can",
        "could",
        "may",
        "might",
        "must",
        "in",
        "on",
        "at",
        "to",
        "of",
        "for",
        "from",
        "by",
        "with",
        "without",
        "about",
        "into",
        "over",
        "under",
        "after",
        "before",
        "between",
        "through",
        "during",
        "up",
        "down",
        "out",
        "off",
        "not",
        "no",
        "yes",
        "all",
        "any",
        "some",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "such",
        "only",
        "own",
        "same",
        "too",
        "very",
        "just",
        "also",
        "then",
        "there",
        "here",
        "when",
        "where",
        "why",
        "how",
        "i have",
    }
)


def normalize_term(term: str) -> str:
    value = unicodedata.normalize("NFKC", term).strip()
    value = re.sub(r"\s+", " ", value)
    if not value:
        raise VocabularyCleanError("empty term")
    return value


def normalize_translation(value: str) -> tuple[list[str], str | None]:
    text = unicodedata.normalize("NFKC", value).strip()
    notes: list[str] = []

    def collect_note(match: re.Match[str]) -> str:
        note = match.group(1).strip()
        if note:
            notes.append(note)
        return " "

    text = _PARENTHESES.sub(collect_note, text)
    parts = [part.strip(_TRIM) for part in _SEPARATORS.split(text) if part.strip(_TRIM)]

    translations: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        translations.append(part)
        if len(translations) >= _MAX_TRANSLATIONS:
            break

    if not translations:
        fallback = text.strip(_TRIM)
        if fallback:
            translations.append(fallback)

    if not translations:
        raise VocabularyCleanError(f"empty translation: {value!r}")

    note_text = "; ".join(dict.fromkeys(notes))[:_MAX_NOTES] or None
    return translations, note_text


def parse_numbered_vocabulary_text(text: str) -> tuple[RawVocabularyEntry, ...]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise VocabularyCleanError("no vocabulary lines found")
    if len(lines) % 3 != 0:
        raise VocabularyCleanError(
            f"expected number/term/translation triples, got {len(lines)} lines"
        )

    entries: list[RawVocabularyEntry] = []
    for offset in range(0, len(lines), 3):
        number, term, translation = lines[offset : offset + 3]
        if not number.isdigit():
            raise VocabularyCleanError(f"expected row number at line {offset + 1}")
        entries.append(
            RawVocabularyEntry(
                term=normalize_term(term),
                translation_ru=translation,
            )
        )
    return tuple(entries)


def load_numbered_vocabulary_text(path: Path) -> tuple[RawVocabularyEntry, ...]:
    return parse_numbered_vocabulary_text(path.read_text(encoding="utf-8"))


def normalize_vocabulary(
    html_result: VocabularyParseResult,
    rtf_entries: tuple[RawVocabularyEntry, ...],
) -> NormalizedVocabularyResult:
    merged: dict[str, _MutableVocabularyEntry] = {}

    for entry in html_result.entries:
        _merge_entry(
            merged,
            entry,
            source="html",
            transcription=entry.transcription,
            frequency=entry.frequency,
            frequency_rank=entry.rank,
        )

    for rtf_entry in rtf_entries:
        _merge_entry(
            merged,
            rtf_entry,
            source="rtf",
            transcription=None,
            frequency=None,
            frequency_rank=None,
        )

    merged_terms = sum(1 for value in merged.values() if len(value.sources) > 1)
    items = [
        NormalizedVocabularyEntry(
            position=0,
            term=value.term,
            translations_ru=tuple(value.translations_ru),
            transcription=value.transcription,
            frequency=value.frequency,
            frequency_rank=value.frequency_rank,
            notes_ru=value.notes_ru,
            sources=tuple(sorted(value.sources)),
            function_word=term_key in FUNCTION_WORDS,
        )
        for term_key, value in merged.items()
    ]

    items.sort(
        key=lambda entry: (
            entry.frequency_rank is None,
            entry.frequency_rank or 0,
            -(entry.frequency or 0),
            entry.term.casefold(),
        )
    )
    positioned = tuple(
        NormalizedVocabularyEntry(
            position=position,
            term=entry.term,
            translations_ru=entry.translations_ru,
            transcription=entry.transcription,
            frequency=entry.frequency,
            frequency_rank=entry.frequency_rank,
            notes_ru=entry.notes_ru,
            sources=entry.sources,
            function_word=entry.function_word,
        )
        for position, entry in enumerate(items, start=1)
    )

    return NormalizedVocabularyResult(
        entries=positioned,
        html_count=html_result.count,
        rtf_count=len(rtf_entries),
        merged_terms=merged_terms,
        html_missing_ranks=html_result.missing_ranks,
        html_duplicate_terms=html_result.duplicate_terms,
    )


def _merge_entry(
    merged: dict[str, _MutableVocabularyEntry],
    entry: VocabularyEntry | RawVocabularyEntry,
    *,
    source: str,
    transcription: str | None,
    frequency: int | None,
    frequency_rank: int | None,
) -> None:
    term = normalize_term(entry.term)
    key = term.casefold()
    translations, notes = normalize_translation(entry.translation_ru)
    value = merged.get(key)

    if value is None:
        merged[key] = _MutableVocabularyEntry(
            term=term,
            translations_ru=translations,
            transcription=transcription,
            frequency=frequency,
            frequency_rank=frequency_rank,
            notes_ru=notes,
            sources={source},
        )
        return

    seen = {item.casefold() for item in value.translations_ru}
    for translation in translations:
        translated_key = translation.casefold()
        if translated_key not in seen and len(value.translations_ru) < _MAX_TRANSLATIONS:
            value.translations_ru.append(translation)
            seen.add(translated_key)

    if notes and notes not in (value.notes_ru or ""):
        combined = f"{value.notes_ru}; {notes}" if value.notes_ru else notes
        value.notes_ru = combined[:_MAX_NOTES]
    value.sources.add(source)
    if value.transcription is None and transcription is not None:
        value.transcription = transcription
    if value.frequency is None and frequency is not None:
        value.frequency = frequency
    if value.frequency_rank is None and frequency_rank is not None:
        value.frequency_rank = frequency_rank
