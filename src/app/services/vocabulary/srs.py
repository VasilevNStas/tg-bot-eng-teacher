"""Simplified SM-2 scheduling for saved vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import Database
from app.db.models import SrsReview, Vocabulary


class ReviewGrade(StrEnum):
    AGAIN = "again"
    GOOD = "good"
    EASY = "easy"


@dataclass(frozen=True, slots=True)
class ReviewCard:
    vocabulary_id: int
    term: str
    translation: str
    context: str | None


class ReviewNotFound(Exception):
    """The vocabulary card does not belong to the user or does not exist."""


class SrsService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def due_cards(
        self,
        user_id: int,
        *,
        limit: int = 10,
        now: datetime | None = None,
    ) -> list[ReviewCard]:
        at = now or datetime.now(UTC)
        async with self._database.session() as session:
            await self._ensure_reviews(session, user_id, at)
            rows = await session.execute(
                select(Vocabulary, SrsReview)
                .join(SrsReview, SrsReview.vocabulary_id == Vocabulary.id)
                .where(
                    Vocabulary.user_id == user_id,
                    SrsReview.due_at <= at,
                )
                .order_by(SrsReview.due_at, SrsReview.id)
                .limit(limit)
            )
            return [
                ReviewCard(
                    vocabulary_id=word.id,
                    term=word.term,
                    translation=word.translation,
                    context=word.context,
                )
                for word, _review in rows
            ]

    async def review(
        self,
        user_id: int,
        vocabulary_id: int,
        grade: ReviewGrade,
        *,
        now: datetime | None = None,
    ) -> datetime:
        at = now or datetime.now(UTC)
        async with self._database.session() as session:
            row = await session.scalar(
                select(SrsReview)
                .join(Vocabulary, Vocabulary.id == SrsReview.vocabulary_id)
                .where(
                    Vocabulary.id == vocabulary_id,
                    Vocabulary.user_id == user_id,
                )
            )
            if row is None:
                raise ReviewNotFound(vocabulary_id)
            schedule(row, grade, at)
            return row.due_at

    @staticmethod
    async def _ensure_reviews(
        session: AsyncSession,
        user_id: int,
        now: datetime,
    ) -> None:
        missing = await session.scalars(
            select(Vocabulary.id)
            .outerjoin(SrsReview, SrsReview.vocabulary_id == Vocabulary.id)
            .where(
                Vocabulary.user_id == user_id,
                SrsReview.id.is_(None),
            )
        )
        for vocabulary_id in missing:
            session.add(
                SrsReview(
                    vocabulary_id=vocabulary_id,
                    due_at=now,
                )
            )


def schedule(review: SrsReview, grade: ReviewGrade, now: datetime) -> None:
    """Apply a simplified SM-2 step in place."""
    review.last_reviewed_at = now
    if grade is ReviewGrade.AGAIN:
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
        if review.reps == 0:
            interval = 1
        elif review.reps == 1:
            interval = 6
        else:
            interval = round(review.interval_days * review.ease)

    review.interval_days = min(365, max(1, interval))
    review.reps += 1
    review.due_at = now + timedelta(days=review.interval_days)
