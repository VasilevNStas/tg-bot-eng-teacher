"""Structured correction payload (ADR-020, B-032).

The LLM returns this JSON shape in chat/correction modes. Keeping it as a
Pydantic model gives us validation, a JSON schema for the provider, and a
single place to evolve the contract.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.db.enums import ErrorCategory


class CorrectionItem(BaseModel):
    """One corrected fragment with a category and a short Russian explanation."""

    model_config = ConfigDict(extra="forbid")

    original: str = Field(min_length=1, description="Learner's original fragment")
    corrected: str = Field(min_length=1, description="Minimal corrected fragment")
    category: ErrorCategory = Field(description="Error category from the fixed taxonomy")
    explanation_ru: str = Field(min_length=1, description="Short explanation in Russian")


class NewVocabularyItem(BaseModel):
    """A useful word/collocation to add to the user's personal list."""

    model_config = ConfigDict(extra="forbid")

    term: str = Field(min_length=1, description="English word or collocation")
    translation_ru: str = Field(min_length=1, description="Russian translation")
    context: str = Field(min_length=1, description="Fragment where the word appeared")


class CorrectionPayload(BaseModel):
    """Complete structured reply for one conversational turn."""

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, description="Conversational reply in English")
    corrections: list[CorrectionItem] = Field(
        description="Significant corrections; empty list if there is nothing to fix"
    )
    new_vocabulary: list[NewVocabularyItem] = Field(
        description="Useful new words; empty list if none"
    )
