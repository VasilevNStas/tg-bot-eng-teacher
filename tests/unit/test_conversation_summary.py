from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.core.config import Settings
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel, ConversationMode, MessageRole
from app.db.models import Conversation, UsageLedger, User
from app.db.models import Message as DbMessage
from app.llm.errors import LLMUnavailable
from app.llm.models import build_routes
from app.llm.port import LLMResponse
from app.services.conversation.service import ConversationService
from app.services.conversation.summary import ConversationSummarizer
from app.services.prompts.builder import PromptBuilder
from app.services.prompts.registry import PromptRegistry
from app.services.usage.service import UsageService

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
        fallback_model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 800,
        response_schema: Any = None,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_schema": response_schema,
            }
        )
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self) -> None:
        return None


def summary_response(text: str = "Learner talked about travel.") -> LLMResponse:
    return LLMResponse(
        content=text,
        parsed=None,
        model="fake/summary",
        input_tokens=20,
        output_tokens=10,
        cost_usd=Decimal("0.0001"),
        latency_ms=5,
    )


def structured_response(*, reply: str = "Great!") -> LLMResponse:
    parsed: dict[str, Any] = {
        "reply": reply,
        "corrections": [],
        "new_vocabulary": [],
    }
    return LLMResponse(
        content=json.dumps(parsed),
        parsed=parsed,
        model="fake/chat",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0.0001"),
        latency_ms=12,
    )


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        bot_token="123456:secret-token",
        llm_api_key="sk-test",
        llm_model_fast="fast/model",
        llm_model_smart="smart/model",
        llm_max_output_tokens=300,
    )


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def make_user(database: Database, tg_id: int = 1) -> User:
    async with database.session() as session:
        user = User(tg_id=tg_id, level=CEFRLevel.B1)
        session.add(user)
        await session.flush()
        return user


async def make_conversation(
    database: Database,
    user: User,
    *,
    mode: ConversationMode = ConversationMode.CHAT,
    summary: str | None = None,
) -> Conversation:
    async with database.session() as session:
        conversation = Conversation(user_id=user.id, mode=mode, summary=summary)
        session.add(conversation)
        await session.flush()
        return conversation


async def add_messages(
    database: Database,
    conversation_id: int,
    pairs: list[tuple[MessageRole, str]],
) -> list[DbMessage]:
    async with database.session() as session:
        rows = [
            DbMessage(conversation_id=conversation_id, role=role, content=content)
            for role, content in pairs
        ]
        session.add_all(rows)
        await session.flush()
        return list(rows)


def make_summarizer(
    database: Database,
    llm: FakeLLM,
    usage: UsageService,
    *,
    history_window: int = 2,
    batch_size: int = 2,
) -> ConversationSummarizer:
    return ConversationSummarizer(
        database,
        llm,
        usage,
        PromptRegistry(PROMPTS_DIR),
        routes=build_routes(make_settings()),
        history_window=history_window,
        batch_size=batch_size,
        max_batch_size=50,
        message_max_chars=800,
        summary_max_chars=2000,
        max_input_tokens=8000,
    )


def make_service(
    database: Database,
    llm: FakeLLM,
    usage: UsageService,
    summarizer: ConversationSummarizer,
    *,
    history_window: int = 2,
    max_conversation_messages: int = 200,
) -> ConversationService:
    builder = PromptBuilder(
        PromptRegistry(PROMPTS_DIR),
        max_input_tokens=8000,
        history_window=history_window,
    )
    return ConversationService(
        database,
        llm,
        usage,
        builder,
        routes=build_routes(make_settings()),
        summarizer=summarizer,
        idle_timeout=timedelta(minutes=30),
        max_conversation_messages=max_conversation_messages,
    )


async def test_rollup_waits_for_batch(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    conversation = await make_conversation(database, user)
    await add_messages(
        database,
        conversation.id,
        [
            (MessageRole.USER, "one"),
            (MessageRole.ASSISTANT, "two"),
            (MessageRole.USER, "three"),
        ],
    )
    llm = FakeLLM()
    summarizer = make_summarizer(
        database,
        llm,
        UsageService(database, daily_budget=10_000),
        history_window=2,
        batch_size=2,
    )

    state = await summarizer.maybe_rollup(conversation)

    assert state.updated is False
    assert llm.calls == []

    await database.dispose()


async def test_rollup_summarizes_only_old_messages(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    conversation = await make_conversation(database, user)
    rows = await add_messages(
        database,
        conversation.id,
        [
            (MessageRole.USER, "old question"),
            (MessageRole.ASSISTANT, "old answer"),
            (MessageRole.USER, "recent question"),
            (MessageRole.ASSISTANT, "recent answer"),
        ],
    )
    llm = FakeLLM(summary_response("Learner asked about travel."))
    summarizer = make_summarizer(
        database,
        llm,
        UsageService(database, daily_budget=10_000),
        history_window=2,
        batch_size=2,
    )

    state = await summarizer.maybe_rollup(conversation)

    assert state.updated is True
    assert state.text == "Learner asked about travel."
    assert state.summarized_until_message_id == rows[1].id
    payload = json.loads(llm.calls[0]["messages"][1].content)
    assert [message["content"] for message in payload["messages"]] == [
        "old question",
        "old answer",
    ]
    assert payload["previous_summary"] == ""

    async with database.session() as session:
        stored = await session.get(Conversation, conversation.id)
        assert stored is not None
        assert stored.summary == "Learner asked about travel."
        assert stored.summarized_until_message_id == rows[1].id
        ledger = (await session.scalars(select(UsageLedger))).one()
        assert ledger.prompt_version is not None
        assert ledger.prompt_version.startswith("summary:")
        assert ledger.conversation_id == conversation.id

    await database.dispose()


async def test_rollup_includes_previous_summary(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    conversation = await make_conversation(database, user, summary="Earlier summary.")
    await add_messages(
        database,
        conversation.id,
        [
            (MessageRole.USER, "old question"),
            (MessageRole.ASSISTANT, "old answer"),
            (MessageRole.USER, "recent question"),
            (MessageRole.ASSISTANT, "recent answer"),
        ],
    )
    llm = FakeLLM(summary_response("Updated summary."))
    summarizer = make_summarizer(
        database,
        llm,
        UsageService(database, daily_budget=10_000),
        history_window=2,
        batch_size=2,
    )

    state = await summarizer.maybe_rollup(conversation)

    assert state.updated is True
    payload = json.loads(llm.calls[0]["messages"][1].content)
    assert payload["previous_summary"] == "Earlier summary."

    await database.dispose()


async def test_rollup_failure_is_non_fatal(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    conversation = await make_conversation(database, user)
    await add_messages(
        database,
        conversation.id,
        [
            (MessageRole.USER, "old question"),
            (MessageRole.ASSISTANT, "old answer"),
            (MessageRole.USER, "recent question"),
            (MessageRole.ASSISTANT, "recent answer"),
        ],
    )
    llm = FakeLLM(LLMUnavailable("down"))
    summarizer = make_summarizer(
        database,
        llm,
        UsageService(database, daily_budget=10_000),
        history_window=2,
        batch_size=2,
    )

    state = await summarizer.maybe_rollup(conversation)

    assert state.updated is False
    async with database.session() as session:
        stored = await session.get(Conversation, conversation.id)
        assert stored is not None
        assert stored.summary is None
        assert stored.summarized_until_message_id is None
        assert int(await session.scalar(select(func.count()).select_from(UsageLedger)) or 0) == 0

    await database.dispose()


async def test_rollup_skips_when_budget_is_exhausted(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    conversation = await make_conversation(database, user)
    await add_messages(
        database,
        conversation.id,
        [
            (MessageRole.USER, "old question"),
            (MessageRole.ASSISTANT, "old answer"),
            (MessageRole.USER, "recent question"),
            (MessageRole.ASSISTANT, "recent answer"),
        ],
    )
    usage = UsageService(database, daily_budget=10)
    await usage.record(
        user_id=user.id,
        model="fake/model",
        input_tokens=10,
        output_tokens=0,
        cost_usd=Decimal("0"),
    )
    llm = FakeLLM()
    summarizer = make_summarizer(database, llm, usage, history_window=2, batch_size=2)

    state = await summarizer.maybe_rollup(conversation)

    assert state.updated is False
    assert llm.calls == []

    await database.dispose()


async def test_finalize_skips_short_conversation(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    conversation = await make_conversation(database, user)
    await add_messages(
        database,
        conversation.id,
        [(MessageRole.USER, "hello"), (MessageRole.ASSISTANT, "hi")],
    )
    llm = FakeLLM()
    summarizer = make_summarizer(
        database,
        llm,
        UsageService(database, daily_budget=10_000),
        batch_size=12,
    )

    state = await summarizer.finalize(conversation)

    assert state.updated is False
    assert llm.calls == []

    await database.dispose()


async def test_finalize_merges_tail_when_summary_exists(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    conversation = await make_conversation(database, user, summary="Earlier summary.")
    rows = await add_messages(
        database,
        conversation.id,
        [(MessageRole.USER, "tail question"), (MessageRole.ASSISTANT, "tail answer")],
    )
    llm = FakeLLM(summary_response("Final summary."))
    summarizer = make_summarizer(database, llm, UsageService(database, daily_budget=10_000))

    state = await summarizer.finalize(conversation)

    assert state.updated is True
    assert state.summarized_until_message_id == rows[-1].id
    payload = json.loads(llm.calls[0]["messages"][1].content)
    assert payload["previous_summary"] == "Earlier summary."
    assert [message["content"] for message in payload["messages"]] == [
        "tail question",
        "tail answer",
    ]

    await database.dispose()


async def test_respond_uses_rollup_summary_in_prompt(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    conversation = await make_conversation(database, user)
    await add_messages(
        database,
        conversation.id,
        [
            (MessageRole.USER, "old question"),
            (MessageRole.ASSISTANT, "old answer"),
            (MessageRole.USER, "recent question"),
            (MessageRole.ASSISTANT, "recent answer"),
        ],
    )
    llm = FakeLLM(summary_response("Learner likes travel."), structured_response(reply="Nice!"))
    usage = UsageService(database, daily_budget=10_000)
    summarizer = make_summarizer(database, llm, usage, history_window=2, batch_size=2)
    service = make_service(database, llm, usage, summarizer)

    turn = await service.respond(user, "next message")

    assert turn.reply == "Nice!"
    chat_messages = llm.calls[1]["messages"]
    assert any(
        "Conversation summary (untrusted data" in message.content
        and "Learner likes travel." in message.content
        for message in chat_messages
    )

    await database.dispose()


async def test_turn_continues_when_rollup_fails(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    conversation = await make_conversation(database, user)
    await add_messages(
        database,
        conversation.id,
        [
            (MessageRole.USER, "old question"),
            (MessageRole.ASSISTANT, "old answer"),
            (MessageRole.USER, "recent question"),
            (MessageRole.ASSISTANT, "recent answer"),
        ],
    )
    llm = FakeLLM(LLMUnavailable("down"), structured_response(reply="Still here."))
    usage = UsageService(database, daily_budget=10_000)
    summarizer = make_summarizer(database, llm, usage, history_window=2, batch_size=2)
    service = make_service(database, llm, usage, summarizer)

    turn = await service.respond(user, "next message")

    assert turn.reply == "Still here."
    async with database.session() as session:
        stored = await session.get(Conversation, conversation.id)
        assert stored is not None
        assert stored.summary is None

    await database.dispose()


async def test_message_cap_closes_conversation(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(structured_response(reply="First reply."))
    usage = UsageService(database, daily_budget=10_000)
    summarizer = make_summarizer(database, llm, usage, batch_size=12)
    service = make_service(
        database,
        llm,
        usage,
        summarizer,
        max_conversation_messages=2,
    )

    turn = await service.respond(user, "hello")

    assert turn.conversation_ended is True
    async with database.session() as session:
        conversation = (await session.scalars(select(Conversation))).one()
        assert conversation.ended_at is not None

    await database.dispose()


async def test_start_new_closes_every_active_conversation(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    chat = await make_conversation(database, user, mode=ConversationMode.CHAT)
    roleplay = await make_conversation(database, user, mode=ConversationMode.ROLEPLAY)
    llm = FakeLLM()
    usage = UsageService(database, daily_budget=10_000)
    summarizer = make_summarizer(database, llm, usage)
    service = make_service(database, llm, usage, summarizer)

    result = await service.start_new(user.id)

    assert result.closed == 2
    assert result.had_roleplay is True
    async with database.session() as session:
        for conversation_id in (chat.id, roleplay.id):
            stored = await session.get(Conversation, conversation_id)
            assert stored is not None
            assert stored.ended_at is not None
    assert llm.calls == []

    await database.dispose()


async def test_rollup_respects_watermark_and_merges_only_new_old_messages(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    conversation = await make_conversation(database, user)
    rows = await add_messages(
        database,
        conversation.id,
        [
            (MessageRole.USER, "u1"),
            (MessageRole.ASSISTANT, "a1"),
            (MessageRole.USER, "u2"),
            (MessageRole.ASSISTANT, "a2"),
        ],
    )
    llm = FakeLLM(summary_response("First summary."), summary_response("Second summary."))
    summarizer = make_summarizer(
        database,
        llm,
        UsageService(database, daily_budget=10_000),
        history_window=2,
        batch_size=2,
    )

    first = await summarizer.maybe_rollup(conversation)
    assert first.updated is True
    assert first.summarized_until_message_id == rows[1].id
    conversation.summary = first.text
    conversation.summarized_until_message_id = first.summarized_until_message_id

    await add_messages(
        database,
        conversation.id,
        [
            (MessageRole.USER, "u3"),
            (MessageRole.ASSISTANT, "a3"),
        ],
    )

    second = await summarizer.maybe_rollup(conversation)

    assert second.updated is True
    assert second.text == "Second summary."
    payload = json.loads(llm.calls[1]["messages"][1].content)
    assert payload["previous_summary"] == "First summary."
    assert [message["content"] for message in payload["messages"]] == ["u2", "a2"]

    await database.dispose()
