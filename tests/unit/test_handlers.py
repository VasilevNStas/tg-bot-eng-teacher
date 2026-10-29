from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, Message, User

from app.bot.commands import BOT_COMMANDS
from app.bot.handlers.common import handle_help, router
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


def make_message(bot: Bot, text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=100, type="private"),
        from_user=User(id=100, is_bot=False, first_name="Tester"),
        text=text,
    ).as_(bot)


def test_common_router_has_two_handlers() -> None:
    assert len(router.message.handlers) == 2


def test_bot_commands_are_unique() -> None:
    commands = [command.command for command in BOT_COMMANDS]

    assert len(commands) == len(set(commands))


async def test_help_replies_with_help_text() -> None:
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)

    await handle_help(make_message(bot, "/help"))

    assert len(session.methods) == 1
    method = session.methods[0]
    assert isinstance(method, SendMessage)
    assert method.text == ru.HELP
    await bot.session.close()
