"""Domain errors for the offline eval harness."""

from __future__ import annotations


class EvalError(Exception):
    """Raised when eval cases, baseline or a live run cannot proceed."""
