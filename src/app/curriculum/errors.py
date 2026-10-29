"""Content layer errors."""

from __future__ import annotations

from app.core.errors import AppError


class ContentError(AppError):
    """Base class for content failures."""


class ContentLoadError(ContentError):
    """A content file is missing, malformed or fails validation."""


class ContentConflictError(ContentError):
    """A topic or item slug is defined more than once in an import batch."""


class ContentReferenceError(ContentError):
    """An item references a topic that does not exist."""
