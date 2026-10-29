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
from aiogram.types import Chat, Message
from aiogram.types import User as TgUser
from sqlalchemy import func, select

from app.bot.formatters import TELEGRAM_LIMIT
from app.bot.handlers.chat import handle_chat
from app.core.config import Settings
from app.core.di import AppServices
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel
from app.db.models import Message as DbMessage
from app.db.models import UsageLedger, User
from app.db.repositories.users import UserRepository
from app.llm.errors import LLMUnavailable
from app.llm.port import LLMResponse
from app.services.conversation.service import ConversationService
from app.services.exercises.service import ExerciseService
from app.services.prompts.builder import PromptBuilder
from app.services.prompts.registry import PromptRegistry
from app.services.usage.service import UsageService
from app.texts import ru

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


class FakeLLM:
    def __init__(self, *responses: LLMResponse | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: Any,
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 800,
        response_schema: Any = None,
    ) -> LLMResponse:
        self.calls.append({"messages": list(messages), "model": model})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

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


def structured_response(
    *,
    reply: str = "Great!",
    corrections: list[dict[str, str]] | None = None,
) -> LLMResponse:
    parsed = {
        "reply": reply,
        "corrections": corrections or [],
        "new_vocabulary": [],
    }
    return LLMResponse(
        content=json.dumps(parsed),
        parsed=parsed,
        model="fake/model",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0.0001"),
        latency_ms=12,
    )


def correction() -> dict[str, str]:
    return {
        "original": "I go",
        "corrected": "I went",
        "category": "tense",
        "explanation_ru": "Past Simple.",
    }


def make_settings(*, budget: int = 10_000) -> Settings:
    return Settings(
        _env_file=None,
        bot_token="123456:secret-token",
        llm_api_key="sk-test",
        llm_model_fast="fast/model",
        llm_model_smart="smart/model",
        daily_token_budget=budget,
    )


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


def make_services(
    database: Database,
    llm: FakeLLM,
    *,
    budget: int = 10_000,
    max_input_tokens: int = 8000,
) -> AppServices:
    settings = make_settings(budget=budget)
    prompts = PromptRegistry(PROMPTS_DIR)
    builder = PromptBuilder(prompts, max_input_tokens=max_input_tokens, history_window=12)
    usage = UsageService(database, daily_budget=budget)
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


def make_message(bot: Bot, text: str, *, user_id: int = 100) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Tester"),
        text=text,
    ).as_(bot)


async def make_bot() -> tuple[Bot, RecordingSession]:
    session = RecordingSession()
    return Bot(token="123456:secret-token", session=session), session


async def make_onboarded_user(
    database: Database,
    *,
    tg_id: int = 100,
    level: CEFRLevel = CEFRLevel.B1,
) -> User:
    async with database.session() as session:
        repository = UserRepository(session)
        user = await repository.get_or_create(tg_id=tg_id, username=None)
        user.onboarding_state = "done"
        user.level = level
    return user


async def count(database: Database, model: type[Any]) -> int:
    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


async def test_chat_requires_onboarding(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    llm = FakeLLM()
    services = make_services(database, llm)
    bot, session = await make_bot()

    await handle_chat(make_message(bot, "hello"), services)

    assert len(session.methods) == 1
    sent = session.methods[0]
    assert isinstance(sent, SendMessage)
    assert sent.text == ru.ONBOARDING_REQUIRED
    assert llm.calls == []

    await bot.session.close()
    await database.dispose()


async def test_chat_sends_reply_with_corrections(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await make_onboarded_user(database)
    services = make_services(database, FakeLLM(structured_response(corrections=[correction()])))
    bot, session = await make_bot()

    await handle_chat(make_message(bot, "I go to cinema"), services, update_id=42)

    assert len(session.methods) == 1
    sent = session.methods[0]
    assert isinstance(sent, SendMessage)
    assert "Great!" in sent.text
    assert "<blockquote expandable>" in sent.text
    assert "I went" in sent.text

    assert await count(database, User) == 1
    assert await count(database, DbMessage) == 2
    assert await count(database, UsageLedger) == 1

    async with database.session() as db_session:
        user = (await db_session.scalars(select(User))).one()
        assert user.tg_id == 100
        assert user.level is CEFRLevel.B1
        assert user.profile == {}
        assert user.last_seen_at is not None
        message = (
            await db_session.scalars(select(DbMessage).order_by(DbMessage.id).limit(1))
        ).one()
        assert message.update_id == 42

    await bot.session.close()
    await database.dispose()


async def test_chat_reuses_existing_user(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await make_onboarded_user(database)
    llm = FakeLLM(structured_response(), structured_response())
    services = make_services(database, llm)
    bot, _session = await make_bot()
    message = make_message(bot, "hello")

    await handle_chat(message, services)
    await handle_chat(message, services)

    assert await count(database, User) == 1
    assert await count(database, DbMessage) == 4

    await bot.session.close()
    await database.dispose()


async def test_chat_reports_exhausted_budget(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    services = make_services(database, FakeLLM(), budget=10)
    async with database.session() as session:
        user = await UserRepository(session).get_or_create(tg_id=100, username=None)
        user.onboarding_state = "done"
    await services.usage.record(
        user_id=user.id,
        model="fake/model",
        input_tokens=10,
        output_tokens=0,
        cost_usd=Decimal("0"),
    )
    bot, session = await make_bot()

    await handle_chat(make_message(bot, "hello"), services)

    assert len(session.methods) == 1
    sent = session.methods[0]
    assert isinstance(sent, SendMessage)
    assert sent.text == ru.BUDGET_EXHAUSTED

    await bot.session.close()
    await database.dispose()


async def test_chat_reports_message_too_long(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await make_onboarded_user(database)
    services = make_services(database, FakeLLM(), max_input_tokens=10)
    bot, session = await make_bot()

    await handle_chat(make_message(bot, "hello"), services)

    assert len(session.methods) == 1
    sent = session.methods[0]
    assert isinstance(sent, SendMessage)
    assert sent.text == ru.MESSAGE_TOO_LONG

    await bot.session.close()
    await database.dispose()


async def test_chat_reports_llm_failure(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await make_onboarded_user(database)
    services = make_services(database, FakeLLM(LLMUnavailable("down")))
    bot, session = await make_bot()

    await handle_chat(make_message(bot, "hello"), services)

    assert len(session.methods) == 1
    sent = session.methods[0]
    assert isinstance(sent, SendMessage)
    assert sent.text == ru.LLM_TEMPORARY_ERROR
    assert await count(database, DbMessage) == 0

    await bot.session.close()
    await database.dispose()


async def test_chat_splits_long_reply_and_corrections(tmp_path: Path) -> None:
    long_reply = "x" * 4050
    database = await make_database(tmp_path)
    await make_onboarded_user(database)
    services = make_services(
        database,
        FakeLLM(structured_response(reply=long_reply, corrections=[correction()])),
    )
    bot, session = await make_bot()

    await handle_chat(make_message(bot, "hello"), services)

    assert len(session.methods) == 2
    first, second = session.methods
    assert isinstance(first, SendMessage)
    assert isinstance(second, SendMessage)
    assert first.text == long_reply
    assert second.text.startswith("<blockquote expandable>")

    await bot.session.close()
    await database.dispose()


async def test_chat_sends_reply_only_when_no_corrections(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await make_onboarded_user(database)
    services = make_services(database, FakeLLM(structured_response()))
    bot, session = await make_bot()

    await handle_chat(make_message(bot, "hello"), services)

    assert len(session.methods) == 1
    sent = session.methods[0]
    assert isinstance(sent, SendMessage)
    assert sent.text == "Great!"
    assert len(sent.text) < TELEGRAM_LIMIT

    await bot.session.close()
    await database.dispose()
