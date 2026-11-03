"""Validated content file schema (ADR-014, ADR-015, B-012)."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.enums import CEFRLevel, ContentKind

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{2,127}$"
BLANK_MARKER = "...."


class PayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IrregularVerbPayload(PayloadBase):
    kind: Literal[ContentKind.IRREGULAR_VERB] = ContentKind.IRREGULAR_VERB
    base: str = Field(min_length=1)
    past: list[str] = Field(min_length=1)
    participle: list[str] = Field(min_length=1)
    translation_ru: str = Field(min_length=1)
    base_ipa: str | None = None
    past_ipa: str | None = None
    participle_ipa: str | None = None

    @model_validator(mode="after")
    def check_forms(self) -> Self:
        for label, forms in (("past", self.past), ("participle", self.participle)):
            if any(not form.strip() for form in forms):
                raise ValueError(f"{label} forms must be non-empty")
            if len({form.casefold() for form in forms}) != len(forms):
                raise ValueError(f"{label} forms must be unique")
        return self


class GapFillPayload(PayloadBase):
    kind: Literal[ContentKind.GAP_FILL] = ContentKind.GAP_FILL
    sentence: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    accepted: list[str] = Field(default_factory=list)
    hint_ru: str | None = None

    @model_validator(mode="after")
    def check_blank(self) -> Self:
        if BLANK_MARKER not in self.sentence:
            raise ValueError(f"sentence must contain '{BLANK_MARKER}'")
        return self


class TranslationPayload(PayloadBase):
    kind: Literal[ContentKind.TRANSLATION_PAIR] = ContentKind.TRANSLATION_PAIR
    prompt_ru: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    accepted: list[str] = Field(default_factory=list)
    notes_ru: str | None = None


class WordOrderPayload(PayloadBase):
    kind: Literal[ContentKind.WORD_ORDER] = ContentKind.WORD_ORDER
    words: list[str] = Field(min_length=2)
    answer: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_answer(self) -> Self:
        if Counter(self.words) != Counter(self.answer.split()):
            raise ValueError("answer must be a permutation of words")
        return self


class MultipleChoicePayload(PayloadBase):
    kind: Literal[ContentKind.MULTIPLE_CHOICE] = ContentKind.MULTIPLE_CHOICE
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=2)
    correct_index: int = Field(ge=0)
    level: CEFRLevel | None = None
    explanation_ru: str | None = None

    @model_validator(mode="after")
    def check_options(self) -> Self:
        if self.correct_index >= len(self.options):
            raise ValueError("correct_index is out of range")
        if len(set(self.options)) != len(self.options):
            raise ValueError("options must be unique")
        return self


class VocabularyPayload(PayloadBase):
    kind: Literal[ContentKind.VOCABULARY] = ContentKind.VOCABULARY
    term: str = Field(min_length=1, max_length=128)
    translations_ru: list[str] = Field(min_length=1, max_length=20)
    accepted_ru: list[str] = Field(default_factory=list, max_length=20)
    accepted_en: list[str] = Field(default_factory=list, max_length=20)
    frequency: int | None = Field(default=None, ge=0)
    frequency_rank: int | None = Field(default=None, ge=1)
    function_word: bool = False
    transcription: str | None = Field(default=None, max_length=128)
    notes_ru: str | None = Field(default=None, max_length=512)
    example_en: str | None = Field(default=None, max_length=512)

    @field_validator("term")
    @classmethod
    def check_term(cls, term: str) -> str:
        term = term.strip()
        if not term:
            raise ValueError("term must be non-empty")
        if "\n" in term or "\r" in term:
            raise ValueError("term must be a single line")
        return term

    @field_validator("translations_ru", "accepted_ru", "accepted_en")
    @classmethod
    def check_variants(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("translations must be non-empty")
        if any(len(value) > 256 for value in cleaned):
            raise ValueError("translation is too long")
        if len({value.casefold() for value in cleaned}) != len(cleaned):
            raise ValueError("translations must be unique")
        return cleaned


ContentPayload = Annotated[
    IrregularVerbPayload
    | GapFillPayload
    | TranslationPayload
    | WordOrderPayload
    | MultipleChoicePayload
    | VocabularyPayload,
    Field(discriminator="kind"),
]


class ContentItemSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=SLUG_PATTERN, max_length=128)
    payload: ContentPayload
    source: str | None = Field(default=None, max_length=256)
    license: str | None = Field(default=None, max_length=32)
    active: bool = True

    @property
    def kind(self) -> ContentKind:
        return self.payload.kind


class TopicSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=SLUG_PATTERN, max_length=64)
    title: str = Field(min_length=1, max_length=128)


class ContentFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: TopicSpec
    source: str | None = Field(default=None, max_length=256)
    license: str = Field(default="unknown", max_length=32)
    items: list[ContentItemSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_unique_slugs(self) -> Self:
        slugs = [item.slug for item in self.items]
        if len(slugs) != len(set(slugs)):
            raise ValueError("item slugs must be unique within a file")
        return self
