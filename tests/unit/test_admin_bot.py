from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, Message, TelegramObject, Update, User

from app.admin_bot.handlers import AdminContext, handle_block, handle_mute, handle_user
from app.admin_bot.middleware import AdminAccessMiddleware, AdminContextMiddleware
from app.core.admin_config import AdminSettings
from app.db.base import Base
from app.db.database import Database
from app.db.models import User as DbUser
from app.services.admin.service import AdminService


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.methods: list[TelegramMethod[Any]] = []

    async def close(self) -> None:
        return None

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,
    ) -> Any:
        self.methods.append(method)
        if isinstance(method, SendMessage):
            return Message(
                message_id=2,
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        return True


def make_settings(**overrides: object) -> AdminSettings:
    values: dict[str, object] = {
        "admin_bot_token": "123456:admin-secret",
        "admin_ids": "100,200",
    }
    values.update(overrides)
    return AdminSettings(_env_file=None, **values)  # type: ignore[arg-type]


def make_update(bot: Bot, *, tg_id: int = 100, chat_type: str = "private") -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=tg_id, type=chat_type),
        from_user=User(id=tg_id, is_bot=False, first_name="Admin"),
        text="/admin",
    ).as_(bot)
    return Update(update_id=1, message=message)


def make_message(bot: Bot, text: str, *, tg_id: int = 100) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=tg_id, type="private"),
        from_user=User(id=tg_id, is_bot=False, first_name="Admin"),
        text=text,
    ).as_(bot)


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        session.add(DbUser(tg_id=100, username="tester"))
    return database


async def test_access_allows_configured_admin_in_private_chat() -> None:
    bot = Bot(token="123456:admin-secret", session=RecordingSession())
    calls = 0

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        return "handled"

    middleware = AdminAccessMiddleware(make_settings())
    assert await middleware(handler, make_update(bot), {}) == "handled"
    assert calls == 1
    await bot.session.close()


async def test_access_ignores_unknown_user_and_group_chat() -> None:
    bot = Bot(token="123456:admin-secret", session=RecordingSession())
    calls = 0

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        return "handled"

    middleware = AdminAccessMiddleware(make_settings())
    assert await middleware(handler, make_update(bot, tg_id=999), {}) is None
    assert await middleware(handler, make_update(bot, chat_type="group"), {}) is None
    assert calls == 0
    await bot.session.close()


async def test_context_middleware_injects_dependencies(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    settings = make_settings(db_path=tmp_path / "bot.db")
    context = AdminContext(AdminService(database), settings)
    middleware = AdminContextMiddleware(context)
    seen: dict[str, Any] = {}

    async def handler(event: TelegramObject, data: dict[str, Any]) -> None:
        seen.update(data)

    await middleware(handler, TelegramObject(), {})
    assert seen["admin_context"] is context
    assert seen["admin_settings"] is settings
    await database.dispose()


async def test_user_command_renders_profile(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    settings = make_settings(db_path=tmp_path / "bot.db")
    context = AdminContext(AdminService(database), settings)
    bot = Bot(token="123456:admin-secret", session=RecordingSession())

    await handle_user(make_message(bot, "/user 100"), context)

    method = bot.session.methods[0]  # type: ignore[attr-defined]
    assert isinstance(method, SendMessage)
    assert "tg_id: 100" in method.text
    await database.dispose()
    await bot.session.close()


async def test_block_command_changes_state_and_audits(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    settings = make_settings(db_path=tmp_path / "bot.db")
    context = AdminContext(AdminService(database), settings)
    bot = Bot(token="123456:admin-secret", session=RecordingSession())

    await handle_block(make_message(bot, "/block 100 spam"), context)

    restriction = await context.service.restriction(100)
    assert restriction is not None and restriction.blocked
    entries = await context.service.recent_audit()
    assert entries[0].action == "block"
    assert entries[0].source == "admin_bot"
    await database.dispose()
    await bot.session.close()


async def test_mute_command_rejects_bad_duration(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    settings = make_settings(db_path=tmp_path / "bot.db")
    context = AdminContext(AdminService(database), settings)
    bot = Bot(token="123456:admin-secret", session=RecordingSession())

    await handle_mute(make_message(bot, "/mute 100 later"), context)

    method = bot.session.methods[0]  # type: ignore[attr-defined]
    assert isinstance(method, SendMessage)
    assert "invalid duration" in method.text
    await database.dispose()
    await bot.session.close()
