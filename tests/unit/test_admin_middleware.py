from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, Message, TelegramObject, Update, User

from app.bot.middlewares.rate_limit import RateLimitMiddleware
from app.bot.middlewares.restriction import RestrictionMiddleware
from app.core.config import RestrictionMode, Settings
from app.db.base import Base
from app.db.database import Database
from app.db.models import User as DbUser
from app.services.admin.service import AdminService
from app.texts import ru


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


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "bot_token": "123456:secret-token",
        "llm_api_key": "sk-test-key",
        "llm_model_fast": "fast",
        "llm_model_smart": "smart",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def make_update(bot: Bot, *, tg_id: int = 100, text: str = "hello") -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=tg_id, type="private"),
        from_user=User(id=tg_id, is_bot=False, first_name="Tester"),
        text=text,
    ).as_(bot)
    return Update(update_id=1, message=message)


async def make_database(tmp_path: Path, *, blocked: bool = False, muted: bool = False) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        user = DbUser(tg_id=100)
        if blocked:
            user.blocked_at = datetime.now(UTC)
            user.blocked_reason = "spam"
        if muted:
            user.muted_until = datetime.now(UTC).replace(year=2099)
            user.mute_reason = "flood"
        session.add(user)
    return database


async def test_blocked_user_is_silent(tmp_path: Path) -> None:
    database = await make_database(tmp_path, blocked=True)
    bot = Bot(token="123456:secret-token", session=RecordingSession())
    calls = 0

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        return "handled"

    middleware = RestrictionMiddleware(
        AdminService(database),
        make_settings(block_mode=RestrictionMode.SILENT),
    )
    result = await middleware(handler, make_update(bot), {})

    assert result is None
    assert calls == 0
    assert bot.session.methods == []  # type: ignore[attr-defined]
    await database.dispose()
    await bot.session.close()


async def test_muted_user_gets_neutral_reply(tmp_path: Path) -> None:
    database = await make_database(tmp_path, muted=True)
    bot = Bot(token="123456:secret-token", session=RecordingSession())
    calls = 0

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        return "handled"

    middleware = RestrictionMiddleware(
        AdminService(database),
        make_settings(mute_mode=RestrictionMode.NEUTRAL),
    )
    await middleware(handler, make_update(bot), {})

    assert calls == 0
    method = bot.session.methods[0]  # type: ignore[attr-defined]
    assert isinstance(method, SendMessage)
    assert method.text == ru.RESTRICTED_NEUTRAL
    await database.dispose()
    await bot.session.close()


async def test_allowed_user_reaches_handler(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    bot = Bot(token="123456:secret-token", session=RecordingSession())
    calls = 0

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        return "handled"

    middleware = RestrictionMiddleware(AdminService(database), make_settings())
    assert await middleware(handler, make_update(bot), {}) == "handled"
    assert calls == 1
    await database.dispose()
    await bot.session.close()


async def test_rate_limit_drops_burst_and_warns_once() -> None:
    bot = Bot(token="123456:secret-token", session=RecordingSession())
    now = [100.0]
    calls = 0

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        return "handled"

    middleware = RateLimitMiddleware(limit=2, clock=lambda: now[0])
    update = make_update(bot)

    assert await middleware(handler, update, {}) == "handled"
    assert await middleware(handler, update, {}) == "handled"
    assert await middleware(handler, update, {}) is None
    assert await middleware(handler, update, {}) is None
    assert calls == 2

    methods = bot.session.methods  # type: ignore[attr-defined]
    assert len(methods) == 1
    assert isinstance(methods[0], SendMessage)
    assert methods[0].text == ru.RATE_LIMITED

    now[0] += 61
    assert await middleware(handler, update, {}) == "handled"
    await bot.session.close()


async def test_rate_limit_is_per_user() -> None:
    bot = Bot(token="123456:secret-token", session=RecordingSession())
    now = [100.0]
    calls = 0

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        return "handled"

    middleware = RateLimitMiddleware(limit=1, clock=lambda: now[0])
    assert await middleware(handler, make_update(bot, tg_id=100), {}) == "handled"
    assert await middleware(handler, make_update(bot, tg_id=100), {}) is None
    assert await middleware(handler, make_update(bot, tg_id=200), {}) == "handled"
    assert calls == 2
    await bot.session.close()
