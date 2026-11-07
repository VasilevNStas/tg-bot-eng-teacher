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
from sqlalchemy import select

from app.bot.callbacks import GoalsCB
from app.bot.factory import create_dispatcher
from app.core.config import Settings
from app.core.di import AppServices
from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel, ConversationMode
from app.db.models import User
from app.db.repositories.users import UserRepository
from app.llm.models import build_routes
from app.llm.port import LLMResponse
from app.services.conversation.service import ConversationService
from app.services.exercises.service import ExerciseService
from app.services.goals.model import (
    DEFAULT_DAILY_ITEMS,
    GoalsProfile,
    render_prompt_block,
)
from app.services.prompts.builder import PromptBuilder, PromptContext
from app.services.prompts.registry import PromptRegistry
from app.services.translation.service import TranslationService
from app.services.usage.service import UsageService
from app.services.words.service import WordCardService
from app.texts import ru

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "prompts"
GOALS_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "content" / "goals_drill.yaml"


class NoopLLM:
    async def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:
        raise AssertionError("LLM must not be called in goals tests")

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
        llm_max_output_tokens=300,
        typing_indicator_enabled=False,
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
        routes=build_routes(settings),
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
            session_size=5,
            rng=random.Random(7),
            topic_slug="goals-drill",
        ),
        translation=TranslationService(database, session_size=1),
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


def make_callback_update(update_id: int, data: GoalsCB, *, user_id: int = 100) -> Update:
    message = Message(
        message_id=update_id,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Tester"),
        text="goals",
    )
    callback = CallbackQuery(
        id=f"cb{update_id}",
        from_user=TgUser(id=user_id, is_bot=False, first_name="Tester"),
        chat_instance="chat-instance",
        message=message,
        data=data.pack(),
    )
    return Update(update_id=update_id, callback_query=callback)


def last_send(session: RecordingSession) -> SendMessage:
    for method in reversed(session.methods):
        if isinstance(method, SendMessage):
            return method
    raise AssertionError("no send_message call")


def last_edit(session: RecordingSession) -> EditMessageText:
    for method in reversed(session.methods):
        if isinstance(method, EditMessageText):
            return method
    raise AssertionError("no edit_message_text call")


async def get_user(database: Database, tg_id: int = 100) -> User:
    async with database.session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
    assert user is not None
    return user


async def seed_goals_user(database: Database, *, daily_items: int = 3) -> None:
    async with database.session() as session:
        repository = UserRepository(session)
        user = await repository.get_or_create(tg_id=100, username=None)
        user.onboarding_state = "done"
        user.level = CEFRLevel.A2
        user.profile = {"goals": {"daily_items": daily_items}}


def test_goals_profile_parses_and_filters_values() -> None:
    profile = GoalsProfile.from_profile(
        {
            "goals": {
                "goals": ["vocabulary", "bogus", "articles", "articles"],
                "interests": ["it", "unknown"],
                "daily_items": 99,
            }
        }
    )

    assert profile.goals == ("articles", "vocabulary")
    assert profile.interests == ("it",)
    assert profile.daily_items == 20


def test_goals_profile_toggle_keeps_canonical_order() -> None:
    profile = GoalsProfile().toggle_goal("vocabulary").toggle_goal("speaking")

    assert profile.goals == ("speaking", "vocabulary")
    assert profile.toggle_goal("speaking").goals == ("vocabulary",)


def test_goals_daily_target_is_optional_and_clamped() -> None:
    profile = GoalsProfile()

    assert profile.daily_items is None
    assert profile.effective_daily_items == DEFAULT_DAILY_ITEMS
    assert profile.with_daily_items(0).daily_items == 1
    assert profile.with_daily_items(50).daily_items == 20
    assert profile.cleared().is_empty


def test_prompt_block_marks_preferences_as_untrusted() -> None:
    block = render_prompt_block(
        {"goals": {"goals": ["articles"], "interests": ["it"], "daily_items": 15}}
    )

    assert block is not None
    assert "untrusted" in block
    assert "articles" in block
    assert "technology" in block
    assert "15 exercises" in block
    assert render_prompt_block({}) is None


def test_prompt_builder_injects_goals_and_keeps_other_profile_json() -> None:
    builder = PromptBuilder(PromptRegistry(PROMPTS_DIR), max_input_tokens=8000)
    prompt = builder.build(
        PromptContext(
            level=CEFRLevel.B1,
            mode=ConversationMode.CHAT,
            user_input="Hello",
            profile={
                "goal": "travel",
                "goals": {"goals": ["articles"], "interests": ["it"], "daily_items": 15},
            },
        )
    )
    contents = "\n".join(message.content for message in prompt.messages)

    assert "Learner goals" in contents
    assert "articles" in contents
    assert "technology" in contents
    assert '"goal":"travel"' in contents
    assert '"goals"' not in contents


async def test_goals_command_and_toggles_persist(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    dispatcher = create_dispatcher(make_services(database))
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)

    await dispatcher.feed_update(bot, make_message_update(1, "/goals"))
    sent = last_send(session)
    assert "Цели обучения" in sent.text
    assert sent.reply_markup is not None

    await dispatcher.feed_update(
        bot,
        make_callback_update(2, GoalsCB(action="toggle_goal", value="articles")),
    )
    await dispatcher.feed_update(
        bot,
        make_callback_update(3, GoalsCB(action="toggle_interest", value="it")),
    )
    await dispatcher.feed_update(
        bot,
        make_callback_update(4, GoalsCB(action="daily", value="15")),
    )

    user = await get_user(database)
    profile = GoalsProfile.from_profile(user.profile)
    assert profile.goals == ("articles",)
    assert profile.interests == ("it",)
    assert profile.daily_items == 15

    await dispatcher.feed_update(bot, make_callback_update(5, GoalsCB(action="done")))
    assert "Цели сохранены" in last_edit(session).text

    await bot.session.close()
    await database.dispose()


async def test_goals_reset_clears_selection(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    dispatcher = create_dispatcher(make_services(database))
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)

    await dispatcher.feed_update(bot, make_message_update(1, "/goals"))
    await dispatcher.feed_update(
        bot,
        make_callback_update(2, GoalsCB(action="toggle_goal", value="articles")),
    )
    await dispatcher.feed_update(bot, make_callback_update(3, GoalsCB(action="reset")))

    user = await get_user(database)
    assert GoalsProfile.from_profile(user.profile).is_empty

    await bot.session.close()
    await database.dispose()


async def test_drill_uses_daily_target(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await ContentImporter(database).import_file(load_content_file(GOALS_FIXTURE))
    await seed_goals_user(database, daily_items=3)

    dispatcher = create_dispatcher(make_services(database))
    session = RecordingSession()
    bot = Bot(token="123456:secret-token", session=session)

    await dispatcher.feed_update(bot, make_message_update(1, "/drill"))

    assert "Вопрос 1 из 3" in last_send(session).text

    await bot.session.close()
    await database.dispose()


def test_goal_labels_cover_all_options() -> None:
    from app.services.goals.model import GOAL_OPTIONS, INTEREST_OPTIONS

    assert set(ru.GOAL_LABELS) == set(GOAL_OPTIONS)
    assert set(ru.INTEREST_LABELS) == set(INTEREST_OPTIONS)
