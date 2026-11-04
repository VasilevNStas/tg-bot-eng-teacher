"""Simplified SM-2 scheduling for saved vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import Database
from app.db.models import Vocabulary


class ReviewGrade(StrEnum):
    """User's self-assessment after seeing a saved word."""

    AGAIN = "again"
    GOOD = "good"
    EASY = "easy"


@dataclass(frozen=True, slots=True)
class ReviewCard:
    """A due vocabulary row projected for the review UI."""

    vocabulary_id: int
    term: str
    translation: str
    context: str | None


class ReviewNotFound(Exception):
    """The vocabulary card does not belong to the user or does not exist."""


class SrsService:
    """Read due cards and apply review grades to the user's saved words."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def due_cards(
        self,
        user_id: int,
        *,
        limit: int = 10,
        now: datetime | None = None,
    ) -> list[ReviewCard]:
        """Return up to ``limit`` due cards, oldest due first.

        Words saved without a schedule get ``due_at = now`` first, so they
        appear immediately instead of disappearing from reviews.
        """
        at = now or datetime.now(UTC)
        async with self._database.session() as session:
            await self._ensure_due(session, user_id, at)
            rows = await session.scalars(
                select(Vocabulary)
                .where(
                    Vocabulary.user_id == user_id,
                    Vocabulary.due_at <= at,
                )
                .order_by(Vocabulary.due_at, Vocabulary.id)
                .limit(limit)
            )
            return [
                ReviewCard(
                    vocabulary_id=word.id,
                    term=word.term,
                    translation=word.translation,
                    context=word.context,
                )
                for word in rows
            ]

    async def review(
        self,
        user_id: int,
        vocabulary_id: int,
        grade: ReviewGrade,
        *,
        now: datetime | None = None,
    ) -> datetime:
        """Apply a grade to one card and return its next due time."""
        at = now or datetime.now(UTC)
        async with self._database.session() as session:
            row = await session.scalar(
                select(Vocabulary).where(
                    Vocabulary.id == vocabulary_id,
                    Vocabulary.user_id == user_id,
                )
            )
            if row is None:
                raise ReviewNotFound(vocabulary_id)
            # A card that was never scheduled is due immediately.
            if row.due_at is None:
                row.due_at = at
            schedule(row, grade, at)
            return row.due_at

    @staticmethod
    async def _ensure_due(
        session: AsyncSession,
        user_id: int,
        now: datetime,
    ) -> None:
        """Mark unscheduled words as due now with one bulk UPDATE.

        Doing this lazily keeps saving a word cheap: the schedule is created
        the first time the user asks for due cards.
        """
        await session.execute(
            update(Vocabulary)
            .where(
                Vocabulary.user_id == user_id,
                Vocabulary.due_at.is_(None),
            )
            .values(due_at=now)
        )


def schedule(review: Vocabulary, grade: ReviewGrade, now: datetime) -> None:
    """Apply a simplified SM-2 step in place.

    ``AGAIN`` resets the streak and schedules a short relearn step.
    ``GOOD``/``EASY`` use the classic 1 -> 6 -> previous * ease progression,
    with ``EASY`` multiplying by an extra 1.3 and nudging ease upward.
    """
    review.last_reviewed_at = now
    if grade is ReviewGrade.AGAIN:
        # Lapse: forget the streak, lower ease, retry in ten minutes.
        review.reps = 0
        review.lapses += 1
        review.interval_days = 0
        review.ease = max(1.3, review.ease - 0.2)
        review.due_at = now + timedelta(minutes=10)
        return

    if grade is ReviewGrade.EASY:
        if review.reps == 0:
            interval = 4
        elif review.reps == 1:
            interval = 10
        else:
            interval = round(review.interval_days * review.ease * 1.3)
        review.ease = min(3.0, review.ease + 0.1)
    else:
        # First two successful reviews use fixed steps, then the SRS curve.
        if review.reps == 0:
            interval = 1
        elif review.reps == 1:
            interval = 6
        else:
            interval = round(review.interval_days * review.ease)

    # Keep intervals within one year so a single "easy" cannot hide a word
    # forever.
    review.interval_days = min(365, max(1, interval))
    review.reps += 1
    review.due_at = now + timedelta(days=review.interval_days)
