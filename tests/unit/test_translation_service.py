from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.db.base import Base
from app.db.database import Database
from app.db.enums import Checker, ExerciseSessionKind, ExerciseState, Verdict
from app.db.models import ContentItem, ExerciseAttempt, ExerciseSession, UsageLedger, User
from app.grading.llm_judge import LLMJudge
from app.llm.errors import LLMUnavailable
from app.llm.models import Route, RouteSpec
from app.llm.port import LLMResponse
from app.services.prompts.registry import PromptRegistry
from app.services.translation.errors import TranslationSessionError, TranslationUnavailable
from app.services.translation.service import TranslationService
from app.services.usage.service import UsageService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "content" / "sample.yaml"


class FakeLLM:
    def __init__(self, *responses: LLMResponse | Exception) -> None:
        self._responses = list(responses)
        self.calls = 0

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
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self) -> None:
        return None


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


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ContentImporter(database).import_file(load_content_file(FIXTURE))
    return database


async def make_user(database: Database, *, tg_id: int = 1) -> User:
    async with database.session() as session:
        user = User(tg_id=tg_id)
        session.add(user)
        await session.flush()
        return user


def make_judge(database: Database, llm: FakeLLM) -> LLMJudge:
    return LLMJudge(
        llm,
        UsageService(database, daily_budget=10_000),
        PromptRegistry(PROJECT_ROOT / "prompts"),
        RouteSpec(
            name=Route.JUDGE,
            model="fake/judge",
            fallback=None,
            temperature=0.0,
            max_tokens=200,
            requires_json=True,
        ),
    )


async def test_start_submit_correct_and_finish(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = TranslationService(database, session_size=1, topic_slug="test-topic-a1")

    session = await service.start_session(user)
    assert len(session.questions) == 1
    question = session.questions[0]
    assert question.prompt_ru == "Я вчера ходил в кино."

    attempt = await service.submit_answer(
        user,
        session_id=session.session_id,
        content_item_id=question.content_item_id,
        answer=question.reference,
    )
    assert attempt.result.verdict is Verdict.CORRECT
    assert attempt.result.checker is Checker.CODE

    stats = await service.finish_session(
        user,
        session.session_id,
        state=ExerciseState.FINISHED,
    )
    assert stats.total == 1
    assert stats.correct == 1

    async with database.session() as db_session:
        persisted = await db_session.scalar(select(ExerciseSession))
    assert persisted is not None
    assert persisted.kind is ExerciseSessionKind.TRANSLATION
    assert persisted.state is ExerciseState.FINISHED

    await database.dispose()


async def test_new_session_abandons_previous(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = TranslationService(database, session_size=1, topic_slug="test-topic-a1")

    first = await service.start_session(user)
    second = await service.start_session(user)

    async with database.session() as db_session:
        first_row = await db_session.get(ExerciseSession, first.session_id)
        second_row = await db_session.get(ExerciseSession, second.session_id)
    assert first_row is not None and first_row.state is ExerciseState.ABANDONED
    assert second_row is not None and second_row.state is ExerciseState.ACTIVE

    await database.dispose()


async def test_mismatch_uses_judge_and_persists_llm_checker(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(judge_response())
    service = TranslationService(
        database,
        judge=make_judge(database, llm),
        session_size=1,
        topic_slug="test-topic-a1",
    )
    session = await service.start_session(user)
    question = session.questions[0]

    attempt = await service.submit_answer(
        user,
        session_id=session.session_id,
        content_item_id=question.content_item_id,
        answer="I go to cinema yesterday.",
    )

    assert attempt.result.verdict is Verdict.PARTIAL
    assert attempt.result.checker is Checker.LLM
    assert attempt.result.explanation is not None
    assert llm.calls == 1

    async with database.session() as db_session:
        persisted = await db_session.scalar(select(ExerciseAttempt))
        usage_count = await db_session.scalar(select(func.count()).select_from(UsageLedger))
    assert persisted is not None and persisted.checker is Checker.LLM
    assert int(usage_count or 0) == 1

    await database.dispose()


async def test_judge_failure_falls_back_to_code(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(LLMUnavailable("down"))
    service = TranslationService(
        database,
        judge=make_judge(database, llm),
        session_size=1,
        topic_slug="test-topic-a1",
    )
    session = await service.start_session(user)
    question = session.questions[0]

    attempt = await service.submit_answer(
        user,
        session_id=session.session_id,
        content_item_id=question.content_item_id,
        answer="I go to cinema yesterday.",
    )

    assert attempt.result.verdict is Verdict.INCORRECT
    assert attempt.result.checker is Checker.CODE
    assert llm.calls == 1

    await database.dispose()


async def test_submit_rejects_non_translation_item(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = TranslationService(database, session_size=1, topic_slug="test-topic-a1")
    session = await service.start_session(user)

    # Pick a known gap-fill item id from the same topic via the DB.
    async with database.session() as db_session:
        gap = await db_session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-gap-present-simple")
        )
    assert gap is not None

    try:
        await service.submit_answer(
            user,
            session_id=session.session_id,
            content_item_id=gap.id,
            answer="goes",
        )
    except TranslationUnavailable:
        pass
    else:
        raise AssertionError("non-translation item must be rejected")

    await database.dispose()


async def test_unauthorized_session_is_rejected(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    other = await make_user(database, tg_id=2)
    service = TranslationService(database, session_size=1, topic_slug="test-topic-a1")
    session = await service.start_session(user)

    try:
        await service.submit_answer(
            other,
            session_id=session.session_id,
            content_item_id=session.questions[0].content_item_id,
            answer="anything",
        )
    except TranslationSessionError:
        pass
    else:
        raise AssertionError("another user's session must be rejected")

    await database.dispose()


async def test_cancel_active_closes_session(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = TranslationService(database, session_size=1, topic_slug="test-topic-a1")
    session = await service.start_session(user)

    stats = await service.cancel_active(user)

    assert stats is not None
    assert stats.total == 0
    async with database.session() as db_session:
        persisted = await db_session.get(ExerciseSession, session.session_id)
    assert persisted is not None and persisted.state is ExerciseState.ABANDONED

    await database.dispose()
