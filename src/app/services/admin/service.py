"""Administrative queries and containment actions.

This service is shared by the separate admin bot and by CLI commands. It never
touches message contents: the admin surface exposes profile fields, progress
counters and restriction state only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.db.database import Database
from app.db.models import (
    AdminAudit,
    ContentItem,
    ExerciseAttempt,
    ExerciseSession,
    User,
    Vocabulary,
    WordProgress,
)


@dataclass(frozen=True, slots=True)
class Restriction:
    """An active block or mute that the user bot must enforce."""

    blocked: bool
    muted: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class AdminCounts:
    """Small dashboard snapshot shown by ``/admin``."""

    users_total: int
    users_new_7d: int
    users_active_7d: int
    content_items: int
    attempts_total: int


@dataclass(frozen=True, slots=True)
class AdminUserInfo:
    """Everything the admin surface may show about one user."""

    tg_id: int
    username: str | None
    level: str
    timezone: str
    onboarding_state: str
    created_at: datetime
    last_seen_at: datetime | None
    blocked_at: datetime | None
    blocked_reason: str | None
    muted_until: datetime | None
    mute_reason: str | None
    attempts: int
    sessions: int
    vocabulary: int
    bank_words_seen: int


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One recent administrative action."""

    id: int
    actor: str
    source: str
    action: str
    target_tg_id: int | None
    details: dict[str, object]
    success: bool
    created_at: datetime


class AdminService:
    """Read-only lookups plus block/mute/unblock with an audit trail."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def restriction(
        self,
        tg_id: int,
        *,
        now: datetime | None = None,
    ) -> Restriction | None:
        """Return an active restriction, or ``None`` when the user may proceed.

        An expired mute is treated as inactive but is not cleared here: the
        middleware runs on the hot path and must stay read-only.
        """
        moment = now or datetime.now(UTC)
        async with self._database.session() as session:
            row = (
                await session.execute(
                    select(
                        User.blocked_at,
                        User.blocked_reason,
                        User.muted_until,
                        User.mute_reason,
                    ).where(User.tg_id == tg_id)
                )
            ).one_or_none()

        if row is None:
            return None
        blocked_at, blocked_reason, muted_until, mute_reason = row
        muted_until = _as_aware(muted_until)
        blocked = blocked_at is not None
        muted = muted_until is not None and muted_until > moment
        if not blocked and not muted:
            return None
        return Restriction(
            blocked=blocked,
            muted=muted,
            reason=blocked_reason if blocked else mute_reason,
        )

    async def get_user(self, tg_id: int) -> AdminUserInfo | None:
        """Load one user with the counters the admin bot displays."""
        async with self._database.session() as session:
            user = await session.scalar(select(User).where(User.tg_id == tg_id))
            if user is None:
                return None
            return await self._user_info(session, user)

    async def list_users(self, *, limit: int = 20) -> list[AdminUserInfo]:
        """Return the most recently seen users (bounded by ``limit``)."""
        async with self._database.session() as session:
            users = list(
                await session.scalars(
                    select(User)
                    .order_by(User.last_seen_at.desc().nulls_last(), User.created_at.desc())
                    .limit(limit)
                )
            )
            return [await self._user_info(session, user) for user in users]

    async def counts(self) -> AdminCounts:
        """Aggregate counters for the dashboard."""
        now = datetime.now(UTC)
        week_ago = now - timedelta(days=7)
        async with self._database.session() as session:
            users_total = await _scalar(session, select(func.count()).select_from(User))
            users_new = await _scalar(
                session,
                select(func.count()).select_from(User).where(User.created_at >= week_ago),
            )
            users_active = await _scalar(
                session,
                select(func.count()).select_from(User).where(User.last_seen_at >= week_ago),
            )
            content_items = await _scalar(
                session,
                select(func.count())
                .select_from(ContentItem)
                .where(ContentItem.is_active.is_(True)),
            )
            attempts = await _scalar(
                session,
                select(func.count()).select_from(ExerciseAttempt),
            )
        return AdminCounts(
            users_total=users_total,
            users_new_7d=users_new,
            users_active_7d=users_active,
            content_items=content_items,
            attempts_total=attempts,
        )

    async def block(
        self,
        tg_id: int,
        *,
        reason: str,
        actor: str,
        source: str,
    ) -> bool:
        """Permanently block a user and record the action."""
        async with self._database.session() as session:
            user = await session.scalar(select(User).where(User.tg_id == tg_id))
            if user is None:
                await self._audit(
                    session,
                    actor=actor,
                    source=source,
                    action="block",
                    target_tg_id=tg_id,
                    details={"reason": reason, "error": "user_not_found"},
                    success=False,
                )
                return False
            user.blocked_at = datetime.now(UTC)
            user.blocked_reason = reason
            user.muted_until = None
            user.mute_reason = None
            await self._audit(
                session,
                actor=actor,
                source=source,
                action="block",
                target_tg_id=tg_id,
                details={"reason": reason},
                success=True,
            )
            return True

    async def unblock(
        self,
        tg_id: int,
        *,
        reason: str = "",
        actor: str,
        source: str,
    ) -> bool:
        """Clear a block and any mute in one action."""
        async with self._database.session() as session:
            user = await session.scalar(select(User).where(User.tg_id == tg_id))
            if user is None:
                await self._audit(
                    session,
                    actor=actor,
                    source=source,
                    action="unblock",
                    target_tg_id=tg_id,
                    details={"reason": reason, "error": "user_not_found"},
                    success=False,
                )
                return False
            user.blocked_at = None
            user.blocked_reason = None
            user.muted_until = None
            user.mute_reason = None
            await self._audit(
                session,
                actor=actor,
                source=source,
                action="unblock",
                target_tg_id=tg_id,
                details={"reason": reason},
                success=True,
            )
            return True

    async def mute(
        self,
        tg_id: int,
        *,
        until: datetime,
        reason: str,
        actor: str,
        source: str,
    ) -> bool:
        """Temporarily restrict a user until ``until``."""
        async with self._database.session() as session:
            user = await session.scalar(select(User).where(User.tg_id == tg_id))
            if user is None:
                await self._audit(
                    session,
                    actor=actor,
                    source=source,
                    action="mute",
                    target_tg_id=tg_id,
                    details={
                        "reason": reason,
                        "until": until.isoformat(),
                        "error": "user_not_found",
                    },
                    success=False,
                )
                return False
            user.muted_until = until
            user.mute_reason = reason
            await self._audit(
                session,
                actor=actor,
                source=source,
                action="mute",
                target_tg_id=tg_id,
                details={"reason": reason, "until": until.isoformat()},
                success=True,
            )
            return True

    async def recent_audit(self, *, limit: int = 20) -> list[AuditEntry]:
        """Return the newest audit rows."""
        async with self._database.session() as session:
            rows = list(
                await session.scalars(
                    select(AdminAudit).order_by(AdminAudit.created_at.desc()).limit(limit)
                )
            )
        return [
            AuditEntry(
                id=row.id,
                actor=row.actor,
                source=row.source,
                action=row.action,
                target_tg_id=row.target_tg_id,
                details=row.details,
                success=row.success,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def _user_info(self, session: AsyncSession, user: User) -> AdminUserInfo:
        """Build a profile snapshot from the given session."""
        attempts = await _scalar(
            session,
            select(func.count())
            .select_from(ExerciseAttempt)
            .where(ExerciseAttempt.user_id == user.id),
        )
        sessions = await _scalar(
            session,
            select(func.count())
            .select_from(ExerciseSession)
            .where(ExerciseSession.user_id == user.id),
        )
        vocabulary = await _scalar(
            session,
            select(func.count()).select_from(Vocabulary).where(Vocabulary.user_id == user.id),
        )
        bank_words = await _scalar(
            session,
            select(func.count()).select_from(WordProgress).where(WordProgress.user_id == user.id),
        )
        return AdminUserInfo(
            tg_id=user.tg_id,
            username=user.username,
            level=user.level.value,
            timezone=user.timezone,
            onboarding_state=user.onboarding_state,
            created_at=user.created_at,
            last_seen_at=user.last_seen_at,
            blocked_at=user.blocked_at,
            blocked_reason=user.blocked_reason,
            muted_until=user.muted_until,
            mute_reason=user.mute_reason,
            attempts=attempts,
            sessions=sessions,
            vocabulary=vocabulary,
            bank_words_seen=bank_words,
        )

    async def _audit(
        self,
        session: AsyncSession,
        *,
        actor: str,
        source: str,
        action: str,
        target_tg_id: int | None,
        details: dict[str, object],
        success: bool,
    ) -> None:
        """Append an audit row inside the caller's transaction."""
        session.add(
            AdminAudit(
                actor=actor,
                source=source,
                action=action,
                target_tg_id=target_tg_id,
                details=details,
                success=success,
            )
        )


async def _scalar(session: AsyncSession, statement: Select[Any]) -> int:
    """Run a COUNT statement and normalise ``None`` to zero."""
    value = await session.scalar(statement)
    return int(value or 0)


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; compare them as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
