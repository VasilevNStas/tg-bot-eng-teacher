"""Minimal vocabulary bank source and compiler.

The word bank is authored in a deliberately small format:

    source: "3000_words.html"
    license: unknown
    words:
      - position: 1
        frequency: 914627
        term: be
        translation_ru: быть

``load_word_bank_path`` compiles these files into the same ``ContentFile``
objects the regular content importer understands, deriving internal fields
(``slug``, ``kind``) that authors should not have to write.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.curriculum.errors import ContentLoadError
from app.curriculum.models import (
    ContentFile,
    ContentItemSpec,
    TopicSpec,
    VocabularyPayload,
)

YAML_SUFFIXES = {".yaml", ".yml"}
_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


class WordEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=1)
    frequency: int = Field(ge=0)
    term: str = Field(min_length=1, max_length=128)
    transcription: str | None = Field(default=None, max_length=128)
    translation_ru: str = Field(min_length=1, max_length=256)
    accepted_ru: list[str] = Field(default_factory=list, max_length=20)
    note_ru: str | None = Field(default=None, max_length=512)

    @field_validator("term", "translation_ru")
    @classmethod
    def check_single_line(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must be non-empty")
        if "\n" in value or "\r" in value:
            raise ValueError("must be a single line")
        return value

    @field_validator("accepted_ru")
    @classmethod
    def check_accepted_ru(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("accepted_ru must not contain empty values")
        if len({value.casefold() for value in cleaned}) != len(cleaned):
            raise ValueError("accepted_ru must be unique")
        return cleaned


class WordBankFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(default="unknown", max_length=256)
    license: str = Field(default="unknown", max_length=32)
    words: list[WordEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_unique_entries(self) -> Self:
        terms = [word.term.casefold() for word in self.words]
        if len(terms) != len(set(terms)):
            raise ValueError("word terms must be unique within a file")

        positions = [word.position for word in self.words]
        if len(positions) != len(set(positions)):
            raise ValueError("word positions must be unique within a file")
        return self


def load_word_bank_file(path: Path) -> WordBankFile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContentLoadError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ContentLoadError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ContentLoadError(f"{path}: top-level YAML value must be a mapping")

    try:
        return WordBankFile.model_validate(raw)
    except ValidationError as exc:
        raise ContentLoadError(f"{path}: {exc}") from exc


def slugify_term(term: str) -> str:
    slug = _SLUG_CLEAN.sub("-", term.casefold()).strip("-")
    return slug or "word"


def compile_word_bank(
    bank: WordBankFile,
    *,
    topic_slug: str,
    topic_title: str | None = None,
    used_slugs: set[str] | None = None,
) -> ContentFile:
    """Turn a minimal word bank into a regular ``ContentFile``.

    ``slug`` and ``kind`` are derived. Vocabulary items intentionally have no
    CEFR level at this stage.
    """
    seen = used_slugs if used_slugs is not None else set()
    items: list[ContentItemSpec] = []
    for word in sorted(bank.words, key=lambda entry: entry.position):
        base_slug = f"word-{slugify_term(word.term)}"
        slug = base_slug
        suffix = 2
        while slug in seen:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        seen.add(slug)

        items.append(
            ContentItemSpec(
                slug=slug,
                payload=VocabularyPayload(
                    term=word.term,
                    translations_ru=[word.translation_ru],
                    accepted_ru=word.accepted_ru,
                    frequency=word.frequency,
                    frequency_rank=word.position,
                    transcription=word.transcription,
                    notes_ru=word.note_ru,
                ),
            )
        )

    topic = TopicSpec(
        slug=topic_slug,
        title=topic_title or topic_slug.replace("-", " ").title(),
    )
    return ContentFile(
        topic=topic,
        source=bank.source,
        license=bank.license,
        items=items,
    )


def load_word_bank_path(path: Path) -> list[tuple[Path, ContentFile]]:
    """Load and compile a word-bank file or a directory of them."""
    files = _collect_files(path)
    banks = [(file_path, load_word_bank_file(file_path)) for file_path in files]

    seen_terms: dict[str, Path] = {}
    seen_slugs: set[str] = set()
    compiled: list[tuple[Path, ContentFile]] = []
    for file_path, bank in banks:
        for word in bank.words:
            key = word.term.casefold()
            previous = seen_terms.get(key)
            if previous is not None:
                raise ContentLoadError(
                    f"duplicate word '{word.term}' in {file_path} and {previous}"
                )
            seen_terms[key] = file_path

        topic_slug = file_path.stem.replace("_", "-")
        compiled.append(
            (
                file_path,
                compile_word_bank(
                    bank,
                    topic_slug=topic_slug,
                    used_slugs=seen_slugs,
                ),
            )
        )
    return compiled


def _collect_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        raise ContentLoadError(f"path does not exist: {path}")
    if not path.is_dir():
        raise ContentLoadError(f"path is neither a file nor a directory: {path}")

    files = sorted(
        candidate
        for candidate in path.iterdir()
        if candidate.is_file() and candidate.suffix.lower() in YAML_SUFFIXES
    )
    if not files:
        raise ContentLoadError(f"no YAML files found in {path}")
    return files
