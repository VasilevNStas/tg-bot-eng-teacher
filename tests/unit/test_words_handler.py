from __future__ import annotations

import random
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageText, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import func, select

from app.bot.callbacks import WordsCB
from app.bot.factory import create_dispatcher
from app.core.config import Settings
from app.core.di import AppServices
from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel, ExerciseSessionKind, ExerciseState, Verdict
from app.db.models import ContentItem, ExerciseAttempt, ExerciseSession, User, WordProgress
from app.db.repositories.users import UserRepository
from app.llm.port import LLMResponse
from app.services.conversation.service import ConversationService
from app.services.exercises.service import ExerciseService
from app.services.prompts.builder import PromptBuilder
from app.services.prompts.registry import PromptRegistry
from app.services.usage.service import UsageService
from app.services.words.service import WordCardService
from app.texts import ru

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "prompts"
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "content" / "vocabulary.yaml"


class NoopLLM:
    async def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:
        raise AssertionError("LLM must not be called during word cards")

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


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def seed_fixture(database: Database) -> None:
    await ContentImporter(database).import_file(load_content_file(FIXTURE))


async def make_user(
    database: Database,
    *,
    tg_id: int = 100,
    profile: dict[str, Any] | None = None,
) -> User:
    async with database.session() as session:
        repository = UserRepository(session)
        user = await repository.get_or_create(tg_id=tg_id, username=None)
        user.onboarding_state = "done"
        user.level = CEFRLevel.A2
        user.profile = profile or {}
    return user


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
        exercise=ExerciseService(database, session_size=1, rng=random.Random(7)),
        words=WordCardService(database, rng=random.Random(7)),
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
    data: WordsCB,
    *,
    user_id: int = 100,
) -> Update:
    callback = CallbackQuery(
        id=f"cb{update_id}",
        from_user=TgUser(id=user_id, is_bot=False, first_name="Tester"),
        chat_instance="chat-instance",
        message=make_message(update_id, "word card", user_id=user_id),
        data=data.pack(),
    )
    return Update(update_id=update_id, callback_query=callback)


async def make_bot() -> tuple[Bot, RecordingSession]:
    session = RecordingSession()
    return Bot(token="123456:secret-token", session=session), session


def sent_texts(session: RecordingSession) -> list[str]:
    return [
        method.text
        for method in session.methods
        if isinstance(method, SendMessage) and method.text is not None
    ]


def edited_texts(session: RecordingSession) -> list[str]:
    return [
        method.text
        for method in session.methods
        if isinstance(method, EditMessageText) and method.text is not None
    ]


async def active_words_session(database: Database) -> ExerciseSession:
    async with database.session() as session:
        exercise_session = await session.scalar(
            select(ExerciseSession)
            .where(
                ExerciseSession.kind == ExerciseSessionKind.WORDS,
                ExerciseSession.state == ExerciseState.ACTIVE,
            )
            .order_by(ExerciseSession.id.desc())
            .limit(1)
        )
    assert exercise_session is not None
    return exercise_session


async def answer_for(database: Database, item_id: int) -> str:
    async with database.session() as session:
        item = await session.get(ContentItem, item_id)
    assert item is not None
    return str(item.payload["translations_ru"][0])


async def count(database: Database, model: type[object]) -> int:
    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


def words_profile(*, size: int = 2, direction: str = "en_ru") -> dict[str, Any]:
    return {"words": {"card_size": size, "direction": direction}}


async def test_words_full_round(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    await make_user(database, profile=words_profile())
    dispatcher = create_dispatcher(make_services(database))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/words"))

    study = sent_texts(session)[-1]
    assert "Карточка слов" in study
    assert "abandon" in study or "care" in study

    exercise_session = await active_words_session(database)
    questions = exercise_session.meta["questions"]
    assert len(questions) == 2
    assert all(question["direction"] == "en_ru" for question in questions)

    await dispatcher.feed_update(
        bot,
        make_callback_update(2, WordsCB(action="quiz")),
    )
    assert "Вопрос 1 из 2" in sent_texts(session)[-1]

    for offset, question in enumerate(questions):
        answer = await answer_for(database, int(question["item_id"]))
        await dispatcher.feed_update(bot, make_message_update(3 + offset, answer))

    texts = sent_texts(session)
    assert any("Верно" in text for text in texts)
    assert any("Карточка завершена" in text for text in texts)
    assert await count(database, ExerciseAttempt) == 2

    async with database.session() as db_session:
        finished = await db_session.get(ExerciseSession, exercise_session.id)
        assert finished is not None
        assert finished.state is ExerciseState.FINISHED
        attempts = list(await db_session.scalars(select(ExerciseAttempt)))
        assert {attempt.verdict for attempt in attempts} == {Verdict.CORRECT}
        progress = list(await db_session.scalars(select(WordProgress)))
        assert len(progress) == 2
        assert all(row.correct_count == 1 for row in progress)

    await bot.session.close()
    await database.dispose()


async def test_words_retry_wrong_items(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    await make_user(database, profile=words_profile())
    dispatcher = create_dispatcher(make_services(database))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/words"))
    exercise_session = await active_words_session(database)
    questions = exercise_session.meta["questions"]

    await dispatcher.feed_update(bot, make_callback_update(2, WordsCB(action="quiz")))
    wrong_id = int(questions[0]["item_id"])
    correct_id = int(questions[1]["item_id"])
    await dispatcher.feed_update(bot, make_message_update(3, "zzz"))
    await dispatcher.feed_update(
        bot,
        make_message_update(4, await answer_for(database, correct_id)),
    )

    await dispatcher.feed_update(bot, make_callback_update(5, WordsCB(action="retry")))

    retry = await active_words_session(database)
    assert retry.id != exercise_session.id
    assert retry.meta["item_ids"] == [wrong_id]
    assert "Карточка слов" in edited_texts(session)[-1]

    await bot.session.close()
    await database.dispose()


async def test_words_settings_change_restarts_card(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    await make_user(database, profile=words_profile(size=2))
    dispatcher = create_dispatcher(make_services(database))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/words"))
    await dispatcher.feed_update(
        bot,
        make_callback_update(2, WordsCB(action="size", value="5")),
    )

    async with database.session() as db_session:
        user = await db_session.scalar(select(User).where(User.tg_id == 100))
    assert user is not None
    assert user.profile["words"]["card_size"] == 5
    assert "Карточка слов" in edited_texts(session)[-1]

    await dispatcher.feed_update(
        bot,
        make_callback_update(3, WordsCB(action="direction", value="ru_en")),
    )
    async with database.session() as db_session:
        user = await db_session.scalar(select(User).where(User.tg_id == 100))
    assert user is not None
    assert user.profile["words"]["direction"] == "ru_en"

    retry_session = await active_words_session(database)
    assert all(question["direction"] == "ru_en" for question in retry_session.meta["questions"])

    await bot.session.close()
    await database.dispose()


async def test_words_requires_onboarding(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    dispatcher = create_dispatcher(make_services(database))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/words"))

    assert sent_texts(session) == [ru.ONBOARDING_REQUIRED]
    assert await count(database, ExerciseSession) == 0

    await bot.session.close()
    await database.dispose()


async def test_words_reports_missing_content(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await make_user(database)
    dispatcher = create_dispatcher(make_services(database))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/words"))

    assert sent_texts(session) == [ru.WORDS_UNAVAILABLE]
    assert await count(database, ExerciseSession) == 0

    await bot.session.close()
    await database.dispose()
