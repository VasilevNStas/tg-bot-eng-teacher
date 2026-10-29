from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel, ConversationMode, ErrorCategory, MessageRole
from app.db.models import Conversation, UsageLedger, User
from app.db.models import Message as DbMessage
from app.llm.errors import LLMUnavailable
from app.llm.port import LLMResponse
from app.services.conversation.service import ConversationService
from app.services.prompts.builder import PromptBuilder
from app.services.prompts.registry import PromptRegistry
from app.services.usage.service import DailyBudgetExceeded, UsageService

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
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_schema": response_schema,
            }
        )
        if not self._responses:
            raise AssertionError("no fake response queued")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self) -> None:
        return None


def structured_response(
    *,
    reply: str = "Nice!",
    corrections: list[dict[str, str]] | None = None,
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> LLMResponse:
    parsed: dict[str, Any] = {
        "reply": reply,
        "corrections": corrections or [],
        "new_vocabulary": [],
    }
    return LLMResponse(
        content=json.dumps(parsed),
        parsed=parsed,
        model="fake/model",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=Decimal("0.0001"),
        latency_ms=12,
    )


def correction() -> dict[str, str]:
    return {
        "original": "I go to cinema",
        "corrected": "I went to the cinema",
        "category": "tense",
        "explanation_ru": "Past Simple для завершённого действия.",
    }


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def make_user(database: Database, tg_id: int = 1) -> User:
    async with database.session() as session:
        user = User(tg_id=tg_id, level=CEFRLevel.B1, profile={"goal": "travel"})
        session.add(user)
    return user


def make_service(
    database: Database,
    llm: FakeLLM,
    usage: UsageService,
    *,
    history_window: int = 12,
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
        model_fast="fast/model",
        model_smart="smart/model",
        max_output_tokens=300,
        idle_timeout=timedelta(minutes=30),
    )


async def count(database: Database, model: type[Any]) -> int:
    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


async def test_first_turn_persists_messages_and_usage(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    usage = UsageService(database, daily_budget=10_000)
    llm = FakeLLM(structured_response(reply="Great!", corrections=[correction()]))
    service = make_service(database, llm, usage)

    turn = await service.respond(user, "I go to cinema", update_id=555)

    assert turn.reply == "Great!"
    assert turn.corrections[0].category is ErrorCategory.TENSE
    assert turn.conversation_id > 0
    assert len(turn.prompt_version) == 12
    assert await count(database, Conversation) == 1
    assert await count(database, DbMessage) == 2
    assert await count(database, UsageLedger) == 1

    async with database.session() as session:
        messages = list(await session.scalars(select(DbMessage).order_by(DbMessage.id)))
        assert messages[0].role is MessageRole.USER
        assert messages[0].content == "I go to cinema"
        assert messages[0].update_id == 555
        assert messages[1].role is MessageRole.ASSISTANT
        assert messages[1].content == "Great!"
        assert messages[1].prompt_version == turn.prompt_version

        ledger = (await session.scalars(select(UsageLedger))).one()
        assert ledger.prompt_version == turn.prompt_version
        assert ledger.input_tokens == 10
        assert ledger.output_tokens == 5
        assert ledger.conversation_id == turn.conversation_id

    await database.dispose()


async def test_second_turn_reuses_conversation_and_history(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(
        structured_response(reply="First reply"),
        structured_response(reply="Second reply"),
    )
    service = make_service(database, llm, UsageService(database, daily_budget=10_000))

    first = await service.respond(user, "hello")
    second = await service.respond(user, "again")

    assert second.conversation_id == first.conversation_id
    assert await count(database, Conversation) == 1
    assert await count(database, DbMessage) == 4

    history = [message.content for message in llm.calls[1]["messages"]]
    assert "hello" in history
    assert "First reply" in history
    assert history[-1] == "again"

    await database.dispose()


async def test_idle_conversation_is_closed_and_replaced(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(
        structured_response(reply="First reply"),
        structured_response(reply="Second reply"),
    )
    service = make_service(database, llm, UsageService(database, daily_budget=10_000))

    first = await service.respond(user, "hello")

    old = datetime.now(UTC) - timedelta(hours=2)
    async with database.session() as session:
        for message in await session.scalars(select(DbMessage)):
            message.created_at = old

    second = await service.respond(user, "again")

    assert second.conversation_id != first.conversation_id
    assert await count(database, Conversation) == 2
    async with database.session() as session:
        previous = await session.get(Conversation, first.conversation_id)
        assert previous is not None
        assert previous.ended_at is not None

    await database.dispose()


async def test_budget_exceeded_does_not_call_llm(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    usage = UsageService(database, daily_budget=10)
    await usage.record(
        user_id=user.id,
        model="fake/model",
        input_tokens=10,
        output_tokens=0,
        cost_usd=Decimal("0"),
    )
    llm = FakeLLM()
    service = make_service(database, llm, usage)

    with pytest.raises(DailyBudgetExceeded):
        await service.respond(user, "hello")

    assert llm.calls == []
    assert await count(database, DbMessage) == 0
    assert await count(database, UsageLedger) == 1

    await database.dispose()


async def test_text_mode_skips_structured_output(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    response = LLMResponse(
        content="Fill the gap: I ___ to school yesterday.",
        parsed=None,
        model="fake/model",
        input_tokens=8,
        output_tokens=6,
        cost_usd=Decimal("0"),
        latency_ms=10,
    )
    llm = FakeLLM(response)
    service = make_service(database, llm, UsageService(database, daily_budget=10_000))

    turn = await service.respond(user, "give me a drill", mode=ConversationMode.DRILL)

    assert llm.calls[0]["response_schema"] is None
    assert turn.reply == response.content
    assert turn.corrections == ()

    await database.dispose()


async def test_correction_mode_uses_smart_model(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(structured_response())
    service = make_service(database, llm, UsageService(database, daily_budget=10_000))

    await service.respond(user, "check this", mode=ConversationMode.CORRECTION)

    assert llm.calls[0]["model"] == "smart/model"

    await database.dispose()


async def test_llm_failure_persists_no_messages_or_usage(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(LLMUnavailable("provider down"))
    service = make_service(database, llm, UsageService(database, daily_budget=10_000))

    with pytest.raises(LLMUnavailable):
        await service.respond(user, "hello")

    assert await count(database, DbMessage) == 0
    assert await count(database, UsageLedger) == 0

    await database.dispose()


async def test_history_window_limits_second_prompt(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(
        structured_response(reply="one"),
        structured_response(reply="two"),
        structured_response(reply="three"),
    )
    service = make_service(
        database, llm, UsageService(database, daily_budget=10_000), history_window=2
    )

    await service.respond(user, "first")
    await service.respond(user, "second")
    await service.respond(user, "third")

    conversation = [m.content for m in llm.calls[2]["messages"] if m.role != "system"]
    assert conversation == ["second", "two", "third"]

    await database.dispose()
