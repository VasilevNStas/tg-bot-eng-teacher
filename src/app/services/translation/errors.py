"""Translation practice errors."""

from __future__ import annotations


class TranslationUnavailable(Exception):
    """The translation bank is missing or has no active items."""


class TranslationSessionError(Exception):
    """A translation session is missing, stale or belongs to another user."""
