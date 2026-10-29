"""Word card service errors."""

from __future__ import annotations

from app.core.errors import AppError


class WordCardUnavailable(AppError):
    """No active vocabulary content is available for a card."""


class WordCardError(AppError):
    """Invalid word card request."""


class WordCardSessionError(AppError):
    """The word card session is missing, foreign or already finished."""
