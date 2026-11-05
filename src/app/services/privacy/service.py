"""User data deletion and retention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import Database
from app.db.enums import CEFRLevel
from app.db.models import (
    Conversation,
    Correction,
    ExerciseAttempt,
    ExerciseSession,
    Reminder,
    UsageLedger,
    User,
    Vocabulary,
    WordProgress,
)
from app.db.models import Message as DbMessage


@dataclass(frozen=True)
class DeletionSummary:
    """How many rows of each type were removed with the account."""

    conversations: int
    messages: int
    corrections: int
    vocabulary: int
    sessions: int
    attempts: int
    reminders: int
    usage: int


@dataclass(frozen=True)
class UserDataSummary:
    """Read-only view of the profile and the user-owned rows kept in the DB."""

    tg_id: int
    username: str | None
    level: CEFRLevel
    timezone: str
    quiet_hours_start: str | None
    quiet_hours_end: str | None
    created_at: datetime
    last_seen_at: datetime | None
    card_size: int | None
    card_direction: str | None
    conversations: int
    messages: int
    corrections: int
    vocabulary: int
    sessions: int
    attempts: int
    word_progress: int
    reminders: int
    usage: int


class PrivacyService:
    """Account deletion and retention pruning."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def has_user(self, tg_id: int) -> bool:
        async with self._database.session() as session:
            user_id = await session.scalar(select(User.id).where(User.tg_id == tg_id))
        return user_id is not None

    async def describe_user(self, tg_id: int) -> UserDataSummary | None:
        """Return what the bot stores about a user, without message contents."""
        async with self._database.session() as session:
            user = await session.scalar(select(User).where(User.tg_id == tg_id))
            if user is None:
                return None

            counts = await self._summary(session, user.id)
            word_progress = await _count(
                session,
                select(func.count())
                .select_from(WordProgress)
                .where(WordProgress.user_id == user.id),
            )
            profile = cast(dict[str, Any], user.profile or {})
            words_value = profile.get("words")
            words = words_value if isinstance(words_value, dict) else {}
            card_size = words.get("card_size")
            card_direction = words.get("direction")

            return UserDataSummary(
                tg_id=user.tg_id,
                username=user.username,
                level=user.level,
                timezone=user.timezone,
                quiet_hours_start=user.quiet_hours_start,
                quiet_hours_end=user.quiet_hours_end,
                created_at=user.created_at,
                last_seen_at=user.last_seen_at,
                card_size=card_size if isinstance(card_size, int) else None,
                card_direction=card_direction if isinstance(card_direction, str) else None,
                conversations=counts.conversations,
                messages=counts.messages,
                corrections=counts.corrections,
                vocabulary=counts.vocabulary,
                sessions=counts.sessions,
                attempts=counts.attempts,
                word_progress=word_progress,
                reminders=counts.reminders,
                usage=counts.usage,
            )

    async def delete_user(self, tg_id: int) -> DeletionSummary | None:
        """Delete the account and all user-owned rows in one transaction.

        Most child tables cascade from ``users``. ``usage_ledger`` uses
        ``SET NULL`` so cost history survives normal account deletion, which
        is why it is deleted explicitly here instead of being orphaned.
        """
        async with self._database.session() as session:
            user = await session.scalar(select(User).where(User.tg_id == tg_id))
            if user is None:
                return None

            summary = await self._summary(session, user.id)
            await session.execute(delete(UsageLedger).where(UsageLedger.user_id == user.id))
            await session.delete(user)
            return summary

    async def prune_history(
        self,
        days: int,
        *,
        now: datetime | None = None,
    ) -> int:
        """Delete conversations older than ``days``; returns deleted count."""
        if days <= 0:
            return 0
        cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
        async with self._database.session() as session:
            # Unfinished conversations fall back to their start time.
            result = await session.execute(
                delete(Conversation).where(
                    func.coalesce(Conversation.ended_at, Conversation.started_at) < cutoff
                )
            )
            return int(getattr(result, "rowcount", 0) or 0)

    async def _summary(self, session: AsyncSession, user_id: int) -> DeletionSummary:
        """Count rows before deletion so the handler can report the result."""
        conversations = await _count(
            session,
            select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id),
        )
        messages = await _count(
            session,
            select(func.count())
            .select_from(DbMessage)
            .join(Conversation, DbMessage.conversation_id == Conversation.id)
            .where(Conversation.user_id == user_id),
        )
        corrections = await _count(
            session,
            select(func.count())
            .select_from(Correction)
            .join(DbMessage, Correction.message_id == DbMessage.id)
            .join(Conversation, DbMessage.conversation_id == Conversation.id)
            .where(Conversation.user_id == user_id),
        )
        vocabulary = await _count(
            session,
            select(func.count()).select_from(Vocabulary).where(Vocabulary.user_id == user_id),
        )
        sessions = await _count(
            session,
            select(func.count())
            .select_from(ExerciseSession)
            .where(ExerciseSession.user_id == user_id),
        )
        attempts = await _count(
            session,
            select(func.count())
            .select_from(ExerciseAttempt)
            .where(ExerciseAttempt.user_id == user_id),
        )
        reminders = await _count(
            session,
            select(func.count()).select_from(Reminder).where(Reminder.user_id == user_id),
        )
        usage = await _count(
            session,
            select(func.count()).select_from(UsageLedger).where(UsageLedger.user_id == user_id),
        )
        return DeletionSummary(
            conversations=conversations,
            messages=messages,
            corrections=corrections,
            vocabulary=vocabulary,
            sessions=sessions,
            attempts=attempts,
            reminders=reminders,
            usage=usage,
        )


async def _count(session: AsyncSession, statement: Select[Any]) -> int:
    """Run a COUNT statement and normalize NULL to zero."""
    value = await session.scalar(statement)
    return int(value or 0)
