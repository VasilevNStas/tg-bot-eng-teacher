from __future__ import annotations

import json
import random
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import func, select

from app.bot.callbacks import MenuCB, TaskCB
from app.bot.factory import create_dispatcher
from app.core.config import Settings
from app.core.di import AppServices
from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel, ExerciseSessionKind, ExerciseState, Verdict
from app.db.models import ExerciseAttempt, ExerciseSession, User
from app.db.repositories.users import UserRepository
from app.grading.llm_judge import LLMJudge
from app.llm.models import Route, RouteSpec, build_routes
from app.llm.port import LLMResponse
from app.services.conversation.service import ConversationService
from app.services.exercises.service import ExerciseService
from app.services.prompts.builder import PromptBuilder
from app.services.prompts.registry import PromptRegistry
from app.services.translation.service import TranslationService
from app.services.usage.service import UsageService
from app.services.words.service import WordCardService
from app.texts import ru

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "prompts"
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "content" / "sample.yaml"


class NoopLLM:
    async def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:
        raise AssertionError("LLM must not be called for a correct translation")

    async def aclose(self) -> None:
        return None


class FakeLLM:
    def __init__(self, *responses: LLMResponse) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:
        self.calls += 1
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
        headers: dict[str, str] | None = None,
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


async def make_user(database: Database, *, onboarded: bool = True) -> User:
    async with database.session() as session:
        repository = UserRepository(session)
        user = await repository.get_or_create(tg_id=100, username=None)
        user.onboarding_state = "done" if onboarded else "new"
        user.level = CEFRLevel.B1
    return user


def judge_response() -> LLMResponse:
    parsed = {
        "verdict": Verdict.PARTIAL.value,
        "explanation_ru": "Смысл верный, но нужен past simple: I went.",
    }
    return LLMResponse(
        content=json.dumps(parsed, ensure_ascii=False),
        parsed=parsed,
        model="fake/judge",
        input_tokens=20,
        output_tokens=10,
        cost_usd=Decimal("0.0001"),
        latency_ms=3,
    )


def make_services(
    database: Database,
    llm: Any,
    *,
    with_judge: bool = False,
) -> AppServices:
    settings = Settings(
        _env_file=None,
        bot_token="123456:secret-token",
        llm_api_key="sk-test",
        llm_model_fast="fast/model",
        llm_model_smart="smart/model",
        llm_max_output_tokens=300,
        typing_indicator_enabled=False,
    )
    prompts = PromptRegistry(PROMPTS_DIR)
    builder = PromptBuilder(prompts, max_input_tokens=8000, history_window=12)
    usage = UsageService(database, daily_budget=10_000)
    conversation = ConversationService(
        database,
        llm,
        usage,
        builder,
        routes=build_routes(settings),
        idle_timeout=timedelta(minutes=30),
    )
    judge = (
        LLMJudge(
            llm,
            usage,
            prompts,
            RouteSpec(
                name=Route.JUDGE,
                model="fake/judge",
                fallback=None,
                temperature=0.0,
                max_tokens=200,
                requires_json=True,
            ),
        )
        if with_judge
        else None
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
        translation=TranslationService(
            database,
            judge=judge,
            session_size=1,
            rng=random.Random(7),
            topic_slug="test-topic-a1",
        ),
        words=WordCardService(database),
    )


def make_message_update(update_id: int, text: str) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=100, type="private"),
            from_user=TgUser(id=100, is_bot=False, first_name="Tester"),
            text=text,
        ),
    )


def make_menu_update(update_id: int, data: MenuCB | TaskCB) -> Update:
    message = Message(
        message_id=update_id,
        date=datetime.now(UTC),
        chat=Chat(id=100, type="private"),
        from_user=TgUser(id=999, is_bot=True, first_name="Bot"),
        text="menu",
    )
    callback = CallbackQuery(
        id=f"cb{update_id}",
        from_user=TgUser(id=100, is_bot=False, first_name="Tester"),
        chat_instance="chat-instance",
        message=message,
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


async def count(database: Database, model: type[object]) -> int:
    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


async def test_translate_full_round_correct(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    await make_user(database)
    dispatcher = create_dispatcher(make_services(database, NoopLLM()))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/translate"))
    question = sent_texts(session)[-1]
    assert "Перевод 1 из 1" in question
    assert "Я вчера ходил в кино." in question

    await dispatcher.feed_update(
        bot,
        make_message_update(2, "I went to the cinema yesterday."),
    )
    texts = sent_texts(session)
    assert any("Отличный перевод" in text for text in texts)
    assert any("Перевод завершён" in text for text in texts)
    assert await count(database, ExerciseAttempt) == 1

    async with database.session() as db_session:
        exercise_session = await db_session.scalar(select(ExerciseSession))
    assert exercise_session is not None
    assert exercise_session.kind is ExerciseSessionKind.TRANSLATION
    assert exercise_session.state is ExerciseState.FINISHED

    await bot.session.close()
    await database.dispose()


async def test_translate_mismatch_shows_judge_explanation(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    await make_user(database)
    llm = FakeLLM(judge_response())
    dispatcher = create_dispatcher(make_services(database, llm, with_judge=True))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/translate"))
    await dispatcher.feed_update(bot, make_message_update(2, "I go to cinema yesterday."))

    texts = sent_texts(session)
    assert any("Почти" in text for text in texts)
    assert any("Комментарий" in text for text in texts)
    assert any("Эталон" in text for text in texts)

    async with database.session() as db_session:
        attempt = await db_session.scalar(select(ExerciseAttempt))
    assert attempt is not None
    assert attempt.verdict is Verdict.PARTIAL

    await bot.session.close()
    await database.dispose()


async def test_menu_action_starts_translation(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    await make_user(database)
    dispatcher = create_dispatcher(make_services(database, NoopLLM()))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_menu_update(1, MenuCB(target="translate")))

    assert "Перевод 1 из 1" in sent_texts(session)[-1]

    await bot.session.close()
    await database.dispose()


async def test_cancel_translation_abandons_session(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    await make_user(database)
    dispatcher = create_dispatcher(make_services(database, NoopLLM()))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/translate"))
    await dispatcher.feed_update(bot, make_message_update(2, "/cancel"))

    assert any("досрочно" in text for text in sent_texts(session))
    async with database.session() as db_session:
        exercise_session = await db_session.scalar(select(ExerciseSession))
    assert exercise_session is not None
    assert exercise_session.state is ExerciseState.ABANDONED

    await bot.session.close()
    await database.dispose()


async def test_translate_requires_onboarding(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_fixture(database)
    await make_user(database, onboarded=False)
    dispatcher = create_dispatcher(make_services(database, NoopLLM()))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/translate"))

    assert sent_texts(session)[-1] == ru.ONBOARDING_REQUIRED

    await bot.session.close()
    await database.dispose()


async def test_translate_reports_missing_bank(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await make_user(database)
    dispatcher = create_dispatcher(make_services(database, NoopLLM()))
    bot, session = await make_bot()

    await dispatcher.feed_update(bot, make_message_update(1, "/translate"))

    assert sent_texts(session)[-1] == ru.TRANSLATION_UNAVAILABLE

    await bot.session.close()
    await database.dispose()
