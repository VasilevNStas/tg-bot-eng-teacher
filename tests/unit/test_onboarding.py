from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import select

from app.bot.callbacks import OnboardingCB
from app.bot.factory import create_dispatcher
from app.core.config import Settings
from app.core.di import AppServices
from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.curriculum.placement import QUESTIONS, load_placement_questions
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel
from app.db.models import User
from app.llm.port import LLMResponse
from app.services.conversation.service import ConversationService
from app.services.exercises.service import ExerciseService
from app.services.prompts.builder import PromptBuilder
from app.services.prompts.registry import PromptRegistry
from app.services.usage.service import UsageService
from app.texts import ru

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
PLACEMENT_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "placement.yaml"


class NoopLLM:
    async def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:
        raise AssertionError("LLM must not be called during onboarding")

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
        if isinstance(method, EditMessageText):
            chat_id = method.chat_id or 100
            return Message(
                message_id=method.message_id,
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="private"),
                text=method.text,
            )
        return True


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


def make_services(database: Database) -> AppServices:
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
    llm = NoopLLM()
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


def make_message(update_id: int, text: str, *, user_id: int = 100) -> Message:
    return Message(
        message_id=update_id,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Tester"),
        text=text,
    )


def make_message_update(update_id: int, text: str, *, user_id: int = 100) -> Update:
    return Update(update_id=update_id, message=make_message(update_id, text, user_id=user_id))


def make_callback_update(
    update_id: int,
    data: OnboardingCB,
    *,
    user_id: int = 100,
) -> Update:
    callback = CallbackQuery(
        id=f"cb{update_id}",
        from_user=TgUser(id=user_id, is_bot=False, first_name="Tester"),
        chat_instance="chat-instance",
        message=make_message(update_id, "onboarding", user_id=user_id),
        data=data.pack(),
    )
    return Update(update_id=update_id, callback_query=callback)


def last_edit(session: RecordingSession) -> EditMessageText:
    for method in reversed(session.methods):
        if isinstance(method, EditMessageText):
            return method
    raise AssertionError("no edit_message_text call")


def last_answer(session: RecordingSession) -> AnswerCallbackQuery:
    for method in reversed(session.methods):
        if isinstance(method, AnswerCallbackQuery):
            return method
    raise AssertionError("no answer_callback_query call")


async def get_user(database: Database, tg_id: int = 100) -> User:
    async with database.session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
    assert user is not None
    return user


async def test_start_creates_user_and_offers_onboarding(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    dispatcher = create_dispatcher(make_services(database))
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)

    await dispatcher.feed_update(bot, make_message_update(1, "/start"))

    sent = session.methods[-1]
    assert isinstance(sent, SendMessage)
    assert sent.text == ru.ONBOARDING_INTRO
    assert sent.reply_markup is not None
    callbacks = [
        OnboardingCB.unpack(button.callback_data)
        for row in sent.reply_markup.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert [callback.action for callback in callbacks] == ["test", "skip"]

    user = await get_user(database)
    assert user.onboarding_state == "new"

    await bot.session.close()
    await database.dispose()


async def test_full_test_sets_highest_level(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    dispatcher = create_dispatcher(make_services(database))
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)

    await dispatcher.feed_update(bot, make_message_update(1, "/start"))
    await dispatcher.feed_update(
        bot,
        make_callback_update(2, OnboardingCB(action="test")),
    )

    assert "Вопрос 1 из 6" in last_edit(session).text

    update_id = 3
    for index, question in enumerate(QUESTIONS):
        await dispatcher.feed_update(
            bot,
            make_callback_update(
                update_id,
                OnboardingCB(
                    action="answer",
                    index=index,
                    value=str(question.correct_index),
                ),
            ),
        )
        update_id += 1

    assert last_edit(session).text == ru.PLACEMENT_RESULT.format(level="C2")

    user = await get_user(database)
    assert user.level is CEFRLevel.C2
    assert user.onboarding_state == "done"

    await bot.session.close()
    await database.dispose()


async def test_full_test_uses_content_bank(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await ContentImporter(database).import_file(load_content_file(PLACEMENT_FIXTURE))
    bank_questions = await load_placement_questions(database)
    assert bank_questions[0].prompt.startswith("BANK:")

    dispatcher = create_dispatcher(make_services(database))
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)

    await dispatcher.feed_update(bot, make_message_update(1, "/start"))
    await dispatcher.feed_update(
        bot,
        make_callback_update(2, OnboardingCB(action="test")),
    )

    assert "BANK: I ___ a student." in last_edit(session).text

    update_id = 3
    for index, question in enumerate(bank_questions):
        await dispatcher.feed_update(
            bot,
            make_callback_update(
                update_id,
                OnboardingCB(
                    action="answer",
                    index=index,
                    value=str(question.correct_index),
                ),
            ),
        )
        update_id += 1

    assert last_edit(session).text == ru.PLACEMENT_RESULT.format(level="C2")
    user = await get_user(database)
    assert user.level is CEFRLevel.C2

    await bot.session.close()
    await database.dispose()


async def test_skip_and_manual_level(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    dispatcher = create_dispatcher(make_services(database))
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)

    await dispatcher.feed_update(bot, make_message_update(1, "/start"))
    await dispatcher.feed_update(
        bot,
        make_callback_update(2, OnboardingCB(action="skip")),
    )
    assert last_edit(session).text == ru.CHOOSE_LEVEL

    await dispatcher.feed_update(
        bot,
        make_callback_update(3, OnboardingCB(action="level", value="B1")),
    )
    assert last_edit(session).text == ru.LEVEL_SET.format(level="B1")

    user = await get_user(database)
    assert user.level is CEFRLevel.B1
    assert user.onboarding_state == "done"

    await bot.session.close()
    await database.dispose()


async def test_stale_answer_is_rejected(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    dispatcher = create_dispatcher(make_services(database))
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)

    await dispatcher.feed_update(bot, make_message_update(1, "/start"))
    await dispatcher.feed_update(
        bot,
        make_callback_update(2, OnboardingCB(action="test")),
    )
    edits_before = sum(isinstance(method, EditMessageText) for method in session.methods)

    await dispatcher.feed_update(
        bot,
        make_callback_update(
            3,
            OnboardingCB(action="answer", index=1, value="0"),
        ),
    )

    assert last_answer(session).text == ru.STALE_QUESTION
    edits_after = sum(isinstance(method, EditMessageText) for method in session.methods)
    assert edits_after == edits_before

    await bot.session.close()
    await database.dispose()
