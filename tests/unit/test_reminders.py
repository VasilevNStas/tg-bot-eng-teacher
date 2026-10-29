from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.db.base import Base
from app.db.database import Database
from app.db.enums import ReminderKind
from app.db.models import Reminder, User
from app.services.reminders.scheduler import ReminderScheduler
from app.services.reminders.service import ReminderService
from app.texts import ru

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def make_user(
    database: Database,
    tg_id: int = 100,
    *,
    timezone: str = "UTC",
    quiet_start: str | None = None,
    quiet_end: str | None = None,
) -> User:
    async with database.session() as session:
        user = User(
            tg_id=tg_id,
            timezone=timezone,
            quiet_hours_start=quiet_start,
            quiet_hours_end=quiet_end,
        )
        session.add(user)
    return user


async def count(database: Database, model: type[object]) -> int:
    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.messages.append((chat_id, text))


async def test_schedule_srs_keeps_one_earliest_pending_reminder(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = ReminderService(database)

    await service.schedule_srs(user.id, NOW + timedelta(hours=2), now=NOW)
    await service.schedule_srs(user.id, NOW + timedelta(hours=1), now=NOW)
    await service.schedule_srs(user.id, NOW + timedelta(hours=3), now=NOW)

    assert await count(database, Reminder) == 1
    due = await service.due(now=NOW + timedelta(hours=4))
    assert len(due) == 1
    assert due[0].tg_id == 100
    assert due[0].kind is ReminderKind.SRS

    await service.mark_sent(due[0].id, now=NOW + timedelta(hours=4))
    assert await service.due(now=NOW + timedelta(hours=5)) == []

    await service.schedule_srs(user.id, NOW + timedelta(days=1), now=NOW)
    assert await count(database, Reminder) == 2

    await database.dispose()


async def test_overdue_reminder_is_moved_after_manual_review(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = ReminderService(database)

    await service.schedule_srs(user.id, NOW - timedelta(minutes=5), now=NOW)
    await service.schedule_srs(user.id, NOW + timedelta(minutes=10), now=NOW)

    due = await service.due(now=NOW + timedelta(minutes=10))
    assert len(due) == 1
    assert due[0].due_at.replace(tzinfo=UTC) == NOW + timedelta(minutes=10)

    await database.dispose()


async def test_scheduler_sends_due_reminder_and_marks_sent(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = ReminderService(database)
    await service.schedule_srs(user.id, NOW - timedelta(minutes=1), now=NOW)
    bot = FakeBot()
    scheduler = ReminderScheduler(bot, service, interval_seconds=60)  # type: ignore[arg-type]

    assert await scheduler.tick(now=NOW) == 1
    assert bot.messages == [(100, ru.REMINDER_SRS)]
    assert await service.due(now=NOW + timedelta(minutes=1)) == []
    assert await scheduler.tick(now=NOW + timedelta(minutes=1)) == 0

    await database.dispose()


async def test_quiet_hours_defer_until_local_morning(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(
        database,
        timezone="Europe/Moscow",
        quiet_start="22:00",
        quiet_end="08:00",
    )
    service = ReminderService(database)
    quiet_now = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)  # 01:00 MSK
    await service.schedule_srs(user.id, quiet_now - timedelta(minutes=1), now=quiet_now)

    assert await service.due(now=quiet_now) == []

    morning = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)  # 08:00 MSK
    due = await service.due(now=morning)
    assert len(due) == 1
    assert due[0].due_at == morning

    await database.dispose()


async def test_scheduler_does_not_send_during_quiet_hours(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(
        database,
        timezone="Europe/Moscow",
        quiet_start="22:00",
        quiet_end="08:00",
    )
    service = ReminderService(database)
    quiet_now = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)
    await service.schedule_srs(user.id, quiet_now - timedelta(minutes=1), now=quiet_now)
    bot = FakeBot()
    scheduler = ReminderScheduler(bot, service, interval_seconds=60)  # type: ignore[arg-type]

    assert await scheduler.tick(now=quiet_now) == 0
    assert bot.messages == []

    morning = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
    assert await scheduler.tick(now=morning) == 1
    assert bot.messages == [(100, ru.REMINDER_SRS)]

    await database.dispose()
