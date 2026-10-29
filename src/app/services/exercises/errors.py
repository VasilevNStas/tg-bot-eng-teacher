"""Exercise service errors."""

from __future__ import annotations

from app.core.errors import AppError


class DrillUnavailable(AppError):
    """No active content is available for a drill."""


class DrillSessionError(AppError):
    """The drill session is missing, foreign or already finished."""
