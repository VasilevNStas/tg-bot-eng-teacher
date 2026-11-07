"""Validated content file schema (ADR-014, ADR-015, B-012).

Every YAML file follows the same shape: a ``topic``, file-level ``source`` and
``license``, and a list of ``items``. Each item is validated by a Pydantic
payload class selected through the ``kind`` discriminator. Keeping validation
here means the importer and the bot only ever see well-formed data.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.enums import CEFRLevel, ContentKind

# Slugs are stable identity keys: lowercase, digits and hyphens only. They are
# used for idempotent upserts, so changing a slug creates a "new" item and
# loses the user's progress for the old one.
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{2,127}$"
# Knowledge tags are goal/interest ids; two-letter ids such as ``it`` are valid.
TAG_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
# Gap-fill sentences must contain a visible placeholder for the missing word.
BLANK_MARKER = "...."


class PayloadBase(BaseModel):
    """Common config for payloads: reject unknown fields so YAML typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class IrregularVerbPayload(PayloadBase):
    """One irregular verb with its past/participle forms and optional IPA."""

    kind: Literal[ContentKind.IRREGULAR_VERB] = ContentKind.IRREGULAR_VERB
    base: str = Field(min_length=1)
    past: list[str] = Field(min_length=1)
    participle: list[str] = Field(min_length=1)
    translation_ru: str = Field(min_length=1)
    base_ipa: str | None = None
    past_ipa: str | None = None
    participle_ipa: str | None = None

    @field_validator("past", "participle", mode="before")
    @classmethod
    def check_forms_input(cls, forms: Any) -> Any:
        """Accept a flat string as a one-element list (``past: was``)."""
        if isinstance(forms, str):
            return [forms]
        return forms

    @model_validator(mode="after")
    def check_forms(self) -> Self:
        """Reject blank or duplicate forms such as ``past: [went, went]``."""
        for label, forms in (("past", self.past), ("participle", self.participle)):
            if any(not form.strip() for form in forms):
                raise ValueError(f"{label} forms must be non-empty")
            if len({form.casefold() for form in forms}) != len(forms):
                raise ValueError(f"{label} forms must be unique")
        return self


class GapFillPayload(PayloadBase):
    """A sentence with a placeholder and the word that fills it."""

    kind: Literal[ContentKind.GAP_FILL] = ContentKind.GAP_FILL
    sentence: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    accepted: list[str] = Field(default_factory=list)
    hint_ru: str | None = None

    @model_validator(mode="after")
    def check_blank(self) -> Self:
        """A gap-fill without a placeholder is unanswerable, so fail at load time."""
        if BLANK_MARKER not in self.sentence:
            raise ValueError(f"sentence must contain '{BLANK_MARKER}'")
        return self


class TranslationPayload(PayloadBase):
    """Translate a Russian prompt; ``reference`` is the model answer."""

    kind: Literal[ContentKind.TRANSLATION_PAIR] = ContentKind.TRANSLATION_PAIR
    prompt_ru: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    accepted: list[str] = Field(default_factory=list)
    notes_ru: str | None = None


class WordOrderPayload(PayloadBase):
    """Reorder shuffled words into a sentence."""

    kind: Literal[ContentKind.WORD_ORDER] = ContentKind.WORD_ORDER
    words: list[str] = Field(min_length=2)
    answer: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_answer(self) -> Self:
        """Ensure the answer uses exactly the provided words (multiset equality)."""
        if Counter(self.words) != Counter(self.answer.split()):
            raise ValueError("answer must be a permutation of words")
        return self


class MultipleChoicePayload(PayloadBase):
    """A question with options; ``correct_index`` points into ``options``."""

    kind: Literal[ContentKind.MULTIPLE_CHOICE] = ContentKind.MULTIPLE_CHOICE
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=2)
    correct_index: int = Field(ge=0)
    # Placement questions carry a level so onboarding can pick an entry point.
    # Regular multiple-choice items may leave it empty.
    level: CEFRLevel | None = None
    explanation_ru: str | None = None

    @model_validator(mode="after")
    def check_options(self) -> Self:
        """Guard against out-of-range or duplicate answer options."""
        if self.correct_index >= len(self.options):
            raise ValueError("correct_index is out of range")
        if len(set(self.options)) != len(self.options):
            raise ValueError("options must be unique")
        return self


class VocabularyPayload(PayloadBase):
    """A word card: the term plus one display translation and grading variants.

    ``translation_ru`` is what the user sees and may contain several senses
    (``"мочь, уметь"``). ``accepted_ru`` lists the individual answers accepted
    for the en→ru direction; when empty, the whole ``translation_ru`` string is
    required. ``frequency``/``transcription`` are optional because not every
    source provides them.
    """

    kind: Literal[ContentKind.VOCABULARY] = ContentKind.VOCABULARY
    term: str = Field(min_length=1, max_length=128)
    translation_ru: str = Field(min_length=1, max_length=512)
    accepted_ru: list[str] = Field(default_factory=list, max_length=20)
    accepted_en: list[str] = Field(default_factory=list, max_length=20)
    frequency: int | None = Field(default=None, ge=0)
    frequency_rank: int | None = Field(default=None, ge=1)
    function_word: bool = False
    transcription: str | None = Field(default=None, max_length=128)
    notes_ru: str | None = Field(default=None, max_length=512)
    example_en: str | None = Field(default=None, max_length=512)

    @field_validator("term", "translation_ru")
    @classmethod
    def check_line(cls, value: str) -> str:
        """Keep display fields single-line: the bot renders them inline."""
        value = value.strip()
        if not value:
            raise ValueError("must be non-empty")
        if "\n" in value or "\r" in value:
            raise ValueError("must be a single line")
        return value

    @field_validator("accepted_ru", "accepted_en")
    @classmethod
    def check_variants(cls, values: list[str]) -> list[str]:
        """Trim variants and reject blanks, overlong values and duplicates."""
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("translations must be non-empty")
        if any(len(value) > 256 for value in cleaned):
            raise ValueError("translation is too long")
        if len({value.casefold() for value in cleaned}) != len(cleaned):
            raise ValueError("translations must be unique")
        return cleaned


class KnowledgePayload(PayloadBase):
    """A curated explanation reused across users.

    Knowledge is admin-authored and tagged with goal/interest ids. The bot
    injects matching entries into the system prompt; raw user dialogues are
    never stored here (ADR-052, ADR-059).
    """

    kind: Literal[ContentKind.KNOWLEDGE] = ContentKind.KNOWLEDGE
    title: str = Field(min_length=1, max_length=128)
    body_md: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(min_length=1, max_length=8)
    level: CEFRLevel | None = None

    @field_validator("title")
    @classmethod
    def check_title(cls, value: str) -> str:
        """Keep the title single-line: it is rendered as a Markdown heading."""
        value = value.strip()
        if not value:
            raise ValueError("title must be non-empty")
        if "\n" in value or "\r" in value:
            raise ValueError("title must be a single line")
        return value

    @field_validator("body_md")
    @classmethod
    def check_body(cls, value: str) -> str:
        """Trim the body and reject whitespace-only explanations."""
        value = value.strip()
        if not value:
            raise ValueError("body_md must be non-empty")
        return value

    @field_validator("tags")
    @classmethod
    def check_tags(cls, tags: list[str]) -> list[str]:
        """Normalize tags and reject unknown shapes or duplicates."""
        cleaned = [tag.strip().casefold() for tag in tags]
        if any(not TAG_PATTERN.fullmatch(tag) for tag in cleaned):
            raise ValueError("tags must be lowercase ids (letters, digits, hyphen)")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("tags must be unique")
        return cleaned


# Discriminated union: Pydantic reads the ``kind`` field and validates the
# matching payload, so callers get a concrete type instead of a loose dict.
ContentPayload = Annotated[
    IrregularVerbPayload
    | GapFillPayload
    | TranslationPayload
    | WordOrderPayload
    | MultipleChoicePayload
    | VocabularyPayload
    | KnowledgePayload,
    Field(discriminator="kind"),
]


# Fields that belong to the item wrapper rather than to its payload.
_ITEM_FIELDS = frozenset({"slug", "payload", "source", "license", "active"})


class ContentItemSpec(BaseModel):
    """One validated learning item inside a content file."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=SLUG_PATTERN, max_length=128)
    payload: ContentPayload
    source: str | None = Field(default=None, max_length=256)
    license: str | None = Field(default=None, max_length=32)
    active: bool = True

    @model_validator(mode="before")
    @classmethod
    def accept_flat_item(cls, data: Any) -> Any:
        """Allow both flat YAML items and the internal ``payload`` form.

        Flat form:

            - slug: irregular-verb-be
              kind: irregular_verb
              base: be
              past: was

        Internal form (used when reconstructing items from the database):

            - slug: irregular-verb-be
              payload: {kind: irregular_verb, base: be, past: [was]}
        """
        if not isinstance(data, dict) or "payload" in data:
            return data

        data = dict(data)
        kind = data.pop("kind", None)
        if kind is None:
            raise ValueError("'kind' is required")
        payload: dict[str, Any] = {"kind": kind}
        for key in list(data):
            if key not in _ITEM_FIELDS:
                payload[key] = data.pop(key)
        data["payload"] = payload
        return data

    @property
    def kind(self) -> ContentKind:
        """Payload kind exposed on the wrapper so callers do not dig into ``payload``."""
        return self.payload.kind


class TopicSpec(BaseModel):
    """Topic metadata; a topic groups related items and is upserted by slug."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=SLUG_PATTERN, max_length=64)
    title: str = Field(min_length=1, max_length=128)


class ContentFile(BaseModel):
    """A whole YAML file: topic metadata, defaults and a list of items.

    File-level ``source``/``license`` act as defaults for items that do not
    specify their own values.
    """

    model_config = ConfigDict(extra="forbid")

    topic: TopicSpec
    source: str | None = Field(default=None, max_length=256)
    license: str = Field(default="unknown", max_length=32)
    items: list[ContentItemSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_unique_slugs(self) -> Self:
        """Slugs are identity keys, so a file cannot define the same item twice."""
        slugs = [item.slug for item in self.items]
        if len(slugs) != len(set(slugs)):
            raise ValueError("item slugs must be unique within a file")
        return self
