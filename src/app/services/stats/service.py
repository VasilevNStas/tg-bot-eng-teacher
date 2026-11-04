"""Aggregated user progress for /stats.

Everything here is read-only and computed on demand from attempts, sessions,
corrections and word progress. There is no separate statistics table: the raw
events are the source of truth, which keeps the schema small.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select

from app.db.database import Database
from app.db.enums import ErrorCategory, ExerciseSessionKind, ExerciseState, Verdict
from app.db.models import (
    ContentItem,
    Conversation,
    Correction,
    ExerciseAttempt,
    ExerciseSession,
    Message,
    Topic,
    Vocabulary,
    WordProgress,
)


@dataclass(frozen=True, slots=True)
class TopicStats:
    """Accuracy for one drill topic."""

    slug: str
    title: str
    attempts: int
    correct: int

    @property
    def accuracy(self) -> int:
        return round(self.correct * 100 / self.attempts) if self.attempts else 0


@dataclass(frozen=True, slots=True)
class WeakWord:
    """A saved chat word with many lapses (SRS state)."""

    term: str
    lapses: int
    ease: float


@dataclass(frozen=True, slots=True)
class ErrorCategoryCount:
    """How often a correction category appeared in chat."""

    category: ErrorCategory
    count: int


@dataclass(frozen=True, slots=True)
class WeakBankWord:
    """A content-bank word with the most wrong answers."""

    term: str
    wrong: int
    attempts: int


@dataclass(frozen=True, slots=True)
class StatsReport:
    """All counters rendered by the /stats handler in one snapshot."""

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
    word_sessions: int
    word_attempts: int
    word_correct: int
    bank_words_seen: int
    weak_bank_words: tuple[WeakBankWord, ...]

    @property
    def accuracy(self) -> int:
        return round(self.correct * 100 / self.attempts_total) if self.attempts_total else 0

    @property
    def word_accuracy(self) -> int:
        return round(self.word_correct * 100 / self.word_attempts) if self.word_attempts else 0


@dataclass(slots=True)
class _TopicAccumulator:
    """Mutable helper for summing GROUP BY rows per topic."""

    title: str
    attempts: int = 0
    correct: int = 0


class StatsService:
    """Build a read-only progress snapshot for one user."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def overview(
        self,
        user_id: int,
        *,
        now: datetime | None = None,
    ) -> StatsReport:
        """Collect all sections in one session with narrow aggregate queries."""
        at = now or datetime.now(UTC)
        async with self._database.session() as session:
            # Overall verdict counts (drills and word cards together).
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

            # Per-topic accuracy: join attempts → sessions → topics.
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

            # Weakest first: lowest accuracy, then most attempts (more signal),
            # then slug for deterministic output.
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
            # A word is due when it was never scheduled (due_at IS NULL) or its
            # scheduled time has passed.
            due_count = int(
                await session.scalar(
                    select(func.count(Vocabulary.id))
                    .select_from(Vocabulary)
                    .where(
                        Vocabulary.user_id == user_id,
                        or_(Vocabulary.due_at.is_(None), Vocabulary.due_at <= at),
                    )
                )
                or 0
            )

            weak_word_rows = await session.execute(
                select(Vocabulary.term, Vocabulary.lapses, Vocabulary.ease)
                .where(
                    Vocabulary.user_id == user_id,
                    Vocabulary.lapses > 0,
                )
                .order_by(Vocabulary.lapses.desc(), Vocabulary.ease, Vocabulary.term)
                .limit(3)
            )
            weak_words = tuple(
                WeakWord(term=term, lapses=int(lapses), ease=float(ease))
                for term, lapses, ease in weak_word_rows
            )

            # Correction categories from the user's chat conversations.
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

            # Best streak across all bank words (not the current streak).
            progress_streak = int(
                await session.scalar(
                    select(func.max(WordProgress.best_streak)).where(
                        WordProgress.user_id == user_id
                    )
                )
                or 0
            )

            word_sessions = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ExerciseSession)
                    .where(
                        ExerciseSession.user_id == user_id,
                        ExerciseSession.kind == ExerciseSessionKind.WORDS,
                        ExerciseSession.state == ExerciseState.FINISHED,
                    )
                )
                or 0
            )
            # Word-card accuracy is computed separately from drill accuracy.
            word_attempt_rows = await session.execute(
                select(ExerciseAttempt.verdict, func.count())
                .join(ExerciseSession, ExerciseSession.id == ExerciseAttempt.session_id)
                .where(
                    ExerciseAttempt.user_id == user_id,
                    ExerciseSession.kind == ExerciseSessionKind.WORDS,
                )
                .group_by(ExerciseAttempt.verdict)
            )
            word_counts = {verdict: int(count) for verdict, count in word_attempt_rows}
            word_attempts = sum(word_counts.values())
            word_correct = word_counts.get(Verdict.CORRECT, 0)

            bank_words_seen = int(
                await session.scalar(
                    select(func.count())
                    .select_from(WordProgress)
                    .where(WordProgress.user_id == user_id)
                )
                or 0
            )
            weak_bank_rows = await session.execute(
                select(
                    ContentItem.payload,
                    WordProgress.wrong_count,
                    WordProgress.attempt_count,
                )
                .join(WordProgress, WordProgress.content_item_id == ContentItem.id)
                .where(
                    WordProgress.user_id == user_id,
                    WordProgress.wrong_count > 0,
                )
                .order_by(WordProgress.wrong_count.desc(), ContentItem.id)
                .limit(3)
            )
            # The term is read from the JSON payload; no extra join is needed.
            weak_bank_words = tuple(
                WeakBankWord(
                    term=str(payload["term"]),
                    wrong=int(wrong),
                    attempts=int(attempts),
                )
                for payload, wrong, attempts in weak_bank_rows
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
            word_sessions=word_sessions,
            word_attempts=word_attempts,
            word_correct=word_correct,
            bank_words_seen=bank_words_seen,
            weak_bank_words=weak_bank_words,
        )
