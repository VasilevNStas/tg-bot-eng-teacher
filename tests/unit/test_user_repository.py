from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel
from app.db.repositories.users import UserRepository


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def test_get_or_create_sets_defaults(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        repository = UserRepository(session)
        user = await repository.get_or_create(tg_id=100, username="tester")

    assert user.id > 0
    assert user.tg_id == 100
    assert user.username == "tester"
    assert user.level is CEFRLevel.A2
    assert user.native_lang == "ru"
    assert user.profile == {}
    assert user.onboarding_state == "new"

    await database.dispose()


async def test_get_or_create_is_idempotent_and_updates_username(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        repository = UserRepository(session)
        first = await repository.get_or_create(tg_id=100, username="old")
        second = await repository.get_or_create(tg_id=100, username="new")

    assert second.id == first.id
    assert second.username == "new"

    async with database.session() as session:
        missing = await UserRepository(session).get_by_tg_id(999)
    assert missing is None

    await database.dispose()


async def test_update_timezone_and_quiet_hours(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        repository = UserRepository(session)
        await repository.get_or_create(tg_id=100, username=None)
        assert await repository.update_timezone(100, "Europe/Berlin")
        assert await repository.update_quiet_hours(100, "22:00", "08:00")
        assert not await repository.update_timezone(999, "UTC")

    async with database.session() as session:
        user = await UserRepository(session).get_by_tg_id(100)
    assert user is not None
    assert user.timezone == "Europe/Berlin"
    assert (user.quiet_hours_start, user.quiet_hours_end) == ("22:00", "08:00")

    await database.dispose()


async def test_touch_sets_last_seen(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    moment = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

    async with database.session() as session:
        user = await UserRepository(session).get_or_create(tg_id=100, username=None)
        UserRepository.touch(user, moment)

    assert user.last_seen_at == moment

    await database.dispose()
