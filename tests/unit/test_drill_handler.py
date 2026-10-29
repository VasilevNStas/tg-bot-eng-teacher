from __future__ import annotations

import random
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import func, select

from app.bot.factory import create_dispatcher
from app.core.config import Settings
from app.core.di import AppServices
from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel, ExerciseState, Verdict
from app.db.models import ExerciseAttempt, ExerciseSession, User, UserProgress
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
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "content" / "sample.yaml"


class NoopLLM:
    async def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:
        raise AssertionError("LLM must not be called during a drill")

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


async def make_user(database: Database, *, tg_id: int = 100) -> User:
    async with database.session() as session:
        repository = UserRepository(session)
        user = await repository.get_or_create(tg_id=tg_id, username=None)
        user.onboarding_state = "done"
        user.level = CEFRLevel.A2
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
        exercise=ExerciseService(
            database,
            session_size=1,
            rng=random.Random(7),
            topic_slug="test-topic-a1",
        ),
        words=WordCardService(database),
    )


def make_message_update(update_id: int, text: str, *, user_id: int = 100) -> Update:
    message = Message(
        message_id=update_id,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Tester"),
        text=text,
    )
    return Update(update_id=update_id, message=message)


async def make_bot() -> tuple[Bot, RecordingSession]:
    session = RecordingSession()
    return Bot(token="123456:secret-token", session=session), session


def sent_texts(session: RecordingSession) -> list[str]:
    return [
        method.text
        for method in session.methods
        if isinstance(method, SendMessage) and method.text is not None
    ]


async def count(database: Database, model: type[object]) -> int:
    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


async def test_drill_full_round_saves_attempt_and_finishes(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    await make_user(database)
    dispatcher = create_dispatcher(make_services(database))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/drill"))

    first = sent_texts(session)[-1]
    assert "Вопрос 1 из 1" in first
    assert "<b>go</b>" in first
    assert ru.DRILL_FORM_PAST in first

    await dispatcher.feed_update(bot, make_message_update(2, "went"))

    texts = sent_texts(session)
    assert any("Верно" in text for text in texts)
    assert any("Тренировка завершена" in text for text in texts)
    assert await count(database, ExerciseAttempt) == 1

    async with database.session() as db_session:
        attempt = await db_session.scalar(select(ExerciseAttempt))
        assert attempt is not None
        assert attempt.verdict is Verdict.CORRECT
        exercise_session = await db_session.get(ExerciseSession, attempt.session_id)
        assert exercise_session is not None
        assert exercise_session.state is ExerciseState.FINISHED
        progress = await db_session.scalar(select(UserProgress))
        assert progress is not None
        assert progress.mastery == 0.2

    await bot.session.close()
    await database.dispose()


async def test_drill_typo_shows_partial_feedback(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    await make_user(database)
    dispatcher = create_dispatcher(make_services(database))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/drill"))
    await dispatcher.feed_update(bot, make_message_update(2, "wnt"))

    assert any("Почти" in text for text in sent_texts(session))
    async with database.session() as db_session:
        attempt = await db_session.scalar(select(ExerciseAttempt))
        assert attempt is not None
        assert attempt.verdict is Verdict.PARTIAL
        progress = await db_session.scalar(select(UserProgress))
        assert progress is not None
        assert progress.mastery == 0.1

    await bot.session.close()
    await database.dispose()


async def test_drill_cancel_abandons_session(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    await make_user(database)
    dispatcher = create_dispatcher(make_services(database))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/drill"))
    await dispatcher.feed_update(bot, make_message_update(2, "/cancel"))

    assert any("досрочно" in text for text in sent_texts(session))
    async with database.session() as db_session:
        exercise_session = await db_session.scalar(select(ExerciseSession))
        assert exercise_session is not None
        assert exercise_session.state is ExerciseState.ABANDONED
        assert exercise_session.finished_at is not None

    await bot.session.close()
    await database.dispose()


async def test_drill_requires_onboarding(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    dispatcher = create_dispatcher(make_services(database))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/drill"))

    assert sent_texts(session) == [ru.ONBOARDING_REQUIRED]
    assert await count(database, ExerciseSession) == 0

    await bot.session.close()
    await database.dispose()


async def test_drill_reports_missing_content(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await make_user(database)
    dispatcher = create_dispatcher(make_services(database))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/drill"))

    assert sent_texts(session) == [ru.DRILL_UNAVAILABLE]
    assert await count(database, ExerciseSession) == 0

    await bot.session.close()
    await database.dispose()
