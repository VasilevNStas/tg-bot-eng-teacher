from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import User as TgUser
from sqlalchemy import select

from app.bot.handlers.preferences import handle_quiet, handle_timezone
from app.db.base import Base
from app.db.database import Database
from app.db.models import User
from app.texts import ru


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


def make_message(text: str) -> MagicMock:
    message = MagicMock()
    message.text = text
    message.from_user = TgUser(id=100, is_bot=False, first_name="Tester")
    message.answer = AsyncMock()
    return message


async def get_user(database: Database) -> User:
    async with database.session() as session:
        user = await session.scalar(select(User).where(User.tg_id == 100))
    assert user is not None
    return user


async def test_timezone_show_set_and_invalid(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    services = SimpleNamespace(database=database)  # type: ignore[arg-type]

    current = make_message("/timezone")
    await handle_timezone(current, services)  # type: ignore[arg-type]
    assert "UTC" in current.answer.call_args.args[0]

    changed = make_message("/timezone Europe/Moscow")
    await handle_timezone(changed, services)  # type: ignore[arg-type]
    assert "Europe/Moscow" in changed.answer.call_args.args[0]
    assert (await get_user(database)).timezone == "Europe/Moscow"

    invalid = make_message("/timezone Nope/Invalid")
    await handle_timezone(invalid, services)  # type: ignore[arg-type]
    assert invalid.answer.call_args.args[0] == ru.TIMEZONE_INVALID
    assert (await get_user(database)).timezone == "Europe/Moscow"

    await database.dispose()


async def test_quiet_show_set_off_and_invalid(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    services = SimpleNamespace(database=database)  # type: ignore[arg-type]

    empty = make_message("/quiet")
    await handle_quiet(empty, services)  # type: ignore[arg-type]
    assert empty.answer.call_args.args[0] == ru.QUIET_CURRENT_OFF

    changed = make_message("/quiet 22:00-08:00")
    await handle_quiet(changed, services)  # type: ignore[arg-type]
    assert changed.answer.call_args.args[0] == ru.QUIET_SET.format(
        start="22:00",
        end="08:00",
        timezone="UTC",
    )
    user = await get_user(database)
    assert (user.quiet_hours_start, user.quiet_hours_end) == ("22:00", "08:00")

    current = make_message("/quiet")
    await handle_quiet(current, services)  # type: ignore[arg-type]
    assert "22:00–08:00" in current.answer.call_args.args[0]

    invalid = make_message("/quiet 22:00-22:00")
    await handle_quiet(invalid, services)  # type: ignore[arg-type]
    assert invalid.answer.call_args.args[0] == ru.QUIET_INVALID

    off = make_message("/quiet off")
    await handle_quiet(off, services)  # type: ignore[arg-type]
    assert off.answer.call_args.args[0] == ru.QUIET_OFF
    user = await get_user(database)
    assert user.quiet_hours_start is None
    assert user.quiet_hours_end is None

    await database.dispose()
