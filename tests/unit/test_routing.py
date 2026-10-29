from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import select

from app.bot.factory import create_dispatcher
from app.core.config import Settings
from app.core.di import AppServices
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel
from app.db.models import Message as DbMessage
from app.db.repositories.users import UserRepository
from app.llm.port import LLMResponse
from app.services.conversation.service import ConversationService
from app.services.exercises.service import ExerciseService
from app.services.prompts.builder import PromptBuilder
from app.services.prompts.registry import PromptRegistry
from app.services.usage.service import UsageService
from app.texts import ru

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


class FakeLLM:
    def __init__(self, *responses: LLMResponse) -> None:
        self._responses = list(responses)

    async def complete(
        self,
        messages: Any,
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 800,
        response_schema: Any = None,
    ) -> LLMResponse:
        return self._responses.pop(0)

    async def aclose(self) -> None:
        return None


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


def structured_response(reply: str = "Great!") -> LLMResponse:
    parsed = {"reply": reply, "corrections": [], "new_vocabulary": []}
    return LLMResponse(
        content=json.dumps(parsed),
        parsed=parsed,
        model="fake/model",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0"),
        latency_ms=10,
    )


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


def make_services(database: Database, llm: FakeLLM) -> AppServices:
    settings = Settings(
        _env_file=None,
        bot_token="123456:secret-token",
        llm_api_key="sk-test",
        llm_model_fast="fast/model",
        llm_model_smart="smart/model",
    )
    prompts = PromptRegistry(PROMPTS_DIR)
    builder = PromptBuilder(prompts, max_input_tokens=8000, history_window=12)
    usage = UsageService(database, daily_budget=10_000)
    conversation = ConversationService(
        database,
        llm,
        usage,
        builder,
        model_fast="fast/model",
        model_smart="smart/model",
        max_output_tokens=300,
        idle_timeout=timedelta(minutes=30),
    )
    return AppServices(
        settings=settings,
        database=database,
        llm=llm,
        usage=usage,
        prompts=prompts,
        builder=builder,
        conversation=conversation,
        exercise=ExerciseService(database, session_size=5),
    )


async def make_onboarded_user(database: Database, *, tg_id: int = 100) -> None:
    async with database.session() as session:
        repository = UserRepository(session)
        user = await repository.get_or_create(tg_id=tg_id, username=None)
        user.onboarding_state = "done"
        user.level = CEFRLevel.B1


def make_update(update_id: int, text: str, *, user_id: int = 100) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=TgUser(id=user_id, is_bot=False, first_name="Tester"),
            text=text,
        ),
    )


async def test_plain_text_routes_to_chat(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await make_onboarded_user(database)
    services = make_services(database, FakeLLM(structured_response("Hello there")))
    dispatcher = create_dispatcher(services)
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)

    await dispatcher.feed_update(bot, make_update(1, "hi"))

    assert len(session.methods) == 1
    sent = session.methods[0]
    assert isinstance(sent, SendMessage)
    assert sent.text == "Hello there"

    async with database.session() as db_session:
        message = (await db_session.scalars(select(DbMessage))).first()
    assert message is not None
    assert message.update_id == 1

    await bot.session.close()
    await database.dispose()


async def test_command_routes_to_common_handler(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    services = make_services(database, FakeLLM())
    dispatcher = create_dispatcher(services)
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)

    await dispatcher.feed_update(bot, make_update(1, "/start"))

    assert len(session.methods) == 1
    sent = session.methods[0]
    assert isinstance(sent, SendMessage)
    assert sent.text == ru.ONBOARDING_INTRO

    await bot.session.close()
    await database.dispose()


async def test_duplicate_update_is_processed_once(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await make_onboarded_user(database)
    services = make_services(database, FakeLLM(structured_response("Only once")))
    dispatcher = create_dispatcher(services)
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)
    update = make_update(1, "hi")

    await dispatcher.feed_update(bot, update)
    await dispatcher.feed_update(bot, update)

    assert len(session.methods) == 1

    await bot.session.close()
    await database.dispose()
