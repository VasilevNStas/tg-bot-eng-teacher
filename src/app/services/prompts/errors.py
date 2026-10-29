"""Prompt layer errors."""

from __future__ import annotations

from app.core.errors import AppError


class PromptError(AppError):
    """Base class for prompt failures."""


class PromptTemplateNotFound(PromptError):
    """A required template file is missing or empty."""


class PromptTooLong(PromptError):
    """Even after trimming, the prompt exceeds the input budget."""
