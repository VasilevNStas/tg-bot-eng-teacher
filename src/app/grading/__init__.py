"""Deterministic grading (ADR-016)."""

from app.grading.grader import (
    GradeResult,
    accepted_answers,
    grade_answer,
    grade_item,
    grade_multiple_choice,
)
from app.grading.normalize import normalize_answer

__all__ = [
    "GradeResult",
    "accepted_answers",
    "grade_answer",
    "grade_item",
    "grade_multiple_choice",
    "normalize_answer",
]
