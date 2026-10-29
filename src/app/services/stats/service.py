"""Aggregated user progress for /stats."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select

from app.db.database import Database
from app.db.enums import ErrorCategory, ExerciseState, Verdict
from app.db.models import (
    Conversation,
    Correction,
    ExerciseAttempt,
    ExerciseSession,
    Message,
    SrsReview,
    Topic,
    UserProgress,
    Vocabulary,
)


@dataclass(frozen=True, slots=True)
class TopicStats:
    slug: str
    title: str
    attempts: int
    correct: int

    @property
    def accuracy(self) -> int:
        return round(self.correct * 100 / self.attempts) if self.attempts else 0


@dataclass(frozen=True, slots=True)
class WeakWord:
    term: str
    lapses: int
    ease: float


@dataclass(frozen=True, slots=True)
class ErrorCategoryCount:
    category: ErrorCategory
    count: int


@dataclass(frozen=True, slots=True)
class StatsReport:
    attempts_total: int
    correct: int
    partial: int
    incorrect: int
    completed_sessions: int
    best_streak: int
    weak_topics: tuple[TopicStats, ...]
    vocabulary_total: int
    due_count: int
    weak_words: tuple[WeakWord, ...]
    error_categories: tuple[ErrorCategoryCount, ...]

    @property
    def accuracy(self) -> int:
        return round(self.correct * 100 / self.attempts_total) if self.attempts_total else 0


@dataclass(slots=True)
class _TopicAccumulator:
    title: str
    attempts: int = 0
    correct: int = 0


class StatsService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def overview(
        self,
        user_id: int,
        *,
        now: datetime | None = None,
    ) -> StatsReport:
        at = now or datetime.now(UTC)
        async with self._database.session() as session:
            attempt_rows = await session.execute(
                select(ExerciseAttempt.verdict, func.count())
                .where(ExerciseAttempt.user_id == user_id)
                .group_by(ExerciseAttempt.verdict)
            )
            counts = {verdict: int(count) for verdict, count in attempt_rows}
            total = sum(counts.values())

            completed_sessions = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ExerciseSession)
                    .where(
                        ExerciseSession.user_id == user_id,
                        ExerciseSession.state == ExerciseState.FINISHED,
                    )
                )
                or 0
            )

            topic_rows = await session.execute(
                select(
                    Topic.slug,
                    Topic.title,
                    ExerciseAttempt.verdict,
                    func.count(),
                )
                .select_from(ExerciseAttempt)
                .join(ExerciseSession, ExerciseSession.id == ExerciseAttempt.session_id)
                .join(Topic, Topic.id == ExerciseSession.topic_id)
                .where(ExerciseAttempt.user_id == user_id)
                .group_by(Topic.slug, Topic.title, ExerciseAttempt.verdict)
            )
            topics: dict[str, _TopicAccumulator] = {}
            for slug, title, verdict, count in topic_rows:
                bucket = topics.setdefault(slug, _TopicAccumulator(title=title))
                bucket.attempts += int(count)
                if verdict is Verdict.CORRECT:
                    bucket.correct += int(count)

            weak_topics = tuple(
                sorted(
                    (
                        TopicStats(
                            slug=slug,
                            title=bucket.title,
                            attempts=bucket.attempts,
                            correct=bucket.correct,
                        )
                        for slug, bucket in topics.items()
                    ),
                    key=lambda item: (item.accuracy, -item.attempts, item.slug),
                )[:3]
            )

            vocabulary_total = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Vocabulary)
                    .where(Vocabulary.user_id == user_id)
                )
                or 0
            )
            due_count = int(
                await session.scalar(
                    select(func.count(Vocabulary.id))
                    .select_from(Vocabulary)
                    .outerjoin(SrsReview, SrsReview.vocabulary_id == Vocabulary.id)
                    .where(
                        Vocabulary.user_id == user_id,
                        or_(SrsReview.id.is_(None), SrsReview.due_at <= at),
                    )
                )
                or 0
            )

            weak_word_rows = await session.execute(
                select(Vocabulary.term, SrsReview.lapses, SrsReview.ease)
                .join(SrsReview, SrsReview.vocabulary_id == Vocabulary.id)
                .where(
                    Vocabulary.user_id == user_id,
                    SrsReview.lapses > 0,
                )
                .order_by(SrsReview.lapses.desc(), SrsReview.ease, Vocabulary.term)
                .limit(3)
            )
            weak_words = tuple(
                WeakWord(term=term, lapses=int(lapses), ease=float(ease))
                for term, lapses, ease in weak_word_rows
            )

            error_rows = await session.execute(
                select(Correction.category, func.count())
                .join(Message, Message.id == Correction.message_id)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(Conversation.user_id == user_id)
                .group_by(Correction.category)
                .order_by(func.count().desc(), Correction.category)
                .limit(3)
            )
            error_categories = tuple(
                ErrorCategoryCount(category=category, count=int(count))
                for category, count in error_rows
            )

            progress_streak = int(
                await session.scalar(
                    select(func.max(UserProgress.streak)).where(UserProgress.user_id == user_id)
                )
                or 0
            )

        return StatsReport(
            attempts_total=total,
            correct=counts.get(Verdict.CORRECT, 0),
            partial=counts.get(Verdict.PARTIAL, 0),
            incorrect=counts.get(Verdict.INCORRECT, 0),
            completed_sessions=completed_sessions,
            weak_topics=weak_topics,
            vocabulary_total=vocabulary_total,
            due_count=due_count,
            weak_words=weak_words,
            error_categories=error_categories,
            best_streak=progress_streak,
        )
