"""User data deletion and retention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import Database
from app.db.models import (
    Conversation,
    Correction,
    Event,
    ExerciseAttempt,
    ExerciseSession,
    Reminder,
    UsageLedger,
    User,
    Vocabulary,
)
from app.db.models import Message as DbMessage


@dataclass(frozen=True)
class DeletionSummary:
    conversations: int
    messages: int
    corrections: int
    vocabulary: int
    sessions: int
    attempts: int
    reminders: int
    usage: int
    events: int


class PrivacyService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def has_user(self, tg_id: int) -> bool:
        async with self._database.session() as session:
            user_id = await session.scalar(select(User.id).where(User.tg_id == tg_id))
        return user_id is not None

    async def delete_user(self, tg_id: int) -> DeletionSummary | None:
        """Delete the account and all user-owned rows in one transaction."""
        async with self._database.session() as session:
            user = await session.scalar(select(User).where(User.tg_id == tg_id))
            if user is None:
                return None

            summary = await self._summary(session, user.id)
            await session.execute(delete(UsageLedger).where(UsageLedger.user_id == user.id))
            await session.execute(delete(Event).where(Event.user_id == user.id))
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
            result = await session.execute(
                delete(Conversation).where(
                    func.coalesce(Conversation.ended_at, Conversation.started_at) < cutoff
                )
            )
            return int(getattr(result, "rowcount", 0) or 0)

    async def _summary(self, session: AsyncSession, user_id: int) -> DeletionSummary:
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
        events = await _count(
            session,
            select(func.count()).select_from(Event).where(Event.user_id == user_id),
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
            events=events,
        )


async def _count(session: AsyncSession, statement: Select[Any]) -> int:
    value = await session.scalar(statement)
    return int(value or 0)
