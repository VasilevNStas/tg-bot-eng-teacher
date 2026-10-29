"""Database-backed reminders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.database import Database
from app.db.enums import ReminderKind
from app.db.models import Reminder, User
from app.services.reminders.quiet import QuietHours, next_allowed_utc, quiet_hours


@dataclass(frozen=True, slots=True)
class DueReminder:
    id: int
    user_id: int
    tg_id: int
    kind: ReminderKind
    payload: dict[str, object]
    due_at: datetime
    timezone: str
    quiet: QuietHours | None


def _as_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class ReminderService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def schedule_srs(
        self,
        user_id: int,
        due_at: datetime,
        *,
        now: datetime | None = None,
    ) -> None:
        """Keep one unsent SRS reminder per user at the earliest useful time."""
        at = now or datetime.now(UTC)
        due = _as_aware(due_at)
        async with self._database.session() as session:
            existing = await session.scalar(
                select(Reminder)
                .where(
                    Reminder.user_id == user_id,
                    Reminder.kind == ReminderKind.SRS,
                    Reminder.sent_at.is_(None),
                )
                .order_by(Reminder.due_at, Reminder.id)
                .limit(1)
            )
            if existing is not None:
                current = _as_aware(existing.due_at)
                if current <= at or due < current:
                    existing.due_at = due
                return

            session.add(
                Reminder(
                    user_id=user_id,
                    due_at=due,
                    kind=ReminderKind.SRS,
                    payload={"source": "srs"},
                )
            )

    async def due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[DueReminder]:
        at = _as_aware(now or datetime.now(UTC))
        async with self._database.session() as session:
            rows = await session.execute(
                select(
                    Reminder,
                    User.tg_id,
                    User.timezone,
                    User.quiet_hours_start,
                    User.quiet_hours_end,
                )
                .join(User, User.id == Reminder.user_id)
                .where(
                    Reminder.sent_at.is_(None),
                    Reminder.due_at <= at,
                )
                .order_by(Reminder.due_at, Reminder.id)
                .limit(limit)
            )
            due: list[DueReminder] = []
            for reminder, tg_id, timezone, quiet_start, quiet_end in rows:
                quiet = quiet_hours(quiet_start, quiet_end)
                deferred = next_allowed_utc(at, timezone, quiet)
                if deferred is not None:
                    reminder.due_at = deferred
                    continue
                due.append(
                    DueReminder(
                        id=reminder.id,
                        user_id=reminder.user_id,
                        tg_id=tg_id,
                        kind=reminder.kind,
                        payload=reminder.payload,
                        due_at=_as_aware(reminder.due_at),
                        timezone=timezone,
                        quiet=quiet,
                    )
                )
            return due

    async def mark_sent(
        self,
        reminder_id: int,
        *,
        now: datetime | None = None,
    ) -> None:
        at = now or datetime.now(UTC)
        async with self._database.session() as session:
            reminder = await session.get(Reminder, reminder_id)
            if reminder is not None and reminder.sent_at is None:
                reminder.sent_at = at
