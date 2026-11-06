from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.curriculum.models import ContentItemSpec, GapFillPayload, TranslationPayload
from app.db.base import Base
from app.db.database import Database
from app.db.enums import Checker, Verdict
from app.db.models import UsageLedger, User
from app.grading.grader import GradeResult
from app.grading.llm_judge import JudgePayload, LLMJudge, grade_translation
from app.llm.errors import LLMInvalidResponse, LLMUnavailable
from app.llm.models import Route, RouteSpec
from app.llm.port import LLMResponse
from app.services.prompts.registry import PromptRegistry
from app.services.usage.service import UsageService

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
                "fallback_model": fallback_model,
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


def translation_item() -> ContentItemSpec:
    return ContentItemSpec.model_validate(
        {
            "slug": "translation-cinema",
            "payload": TranslationPayload(
                prompt_ru="Я вчера ходил в кино.",
                reference="I went to the cinema yesterday.",
                accepted=["Yesterday I went to the cinema"],
            ),
        }
    )


def judge_response(
    *,
    verdict: Verdict = Verdict.PARTIAL,
    explanation: str = "Нужен past simple: I went to the cinema yesterday.",
) -> LLMResponse:
    parsed = {"verdict": verdict.value, "explanation_ru": explanation}
    return LLMResponse(
        content=json.dumps(parsed, ensure_ascii=False),
        parsed=parsed,
        model="fake/judge",
        input_tokens=30,
        output_tokens=10,
        cost_usd=Decimal("0.0002"),
        latency_ms=7,
    )


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def make_user(database: Database) -> User:
    async with database.session() as session:
        user = User(tg_id=1)
        session.add(user)
        await session.flush()
        return user


def make_judge(
    database: Database,
    llm: FakeLLM,
    *,
    daily_budget: int = 100_000,
    monthly_budget: Decimal = Decimal("2.00"),
) -> tuple[LLMJudge, UsageService]:
    usage = UsageService(
        database,
        daily_budget=daily_budget,
        monthly_budget_usd=monthly_budget,
    )
    route = RouteSpec(
        name=Route.JUDGE,
        model="fake/judge",
        fallback=None,
        temperature=0.0,
        max_tokens=200,
        requires_json=True,
    )
    judge = LLMJudge(
        llm,
        usage,
        PromptRegistry(PROJECT_ROOT / "prompts"),
        route,
    )
    return judge, usage


async def count_usage(database: Database) -> int:
    async with database.session() as session:
        count = await session.scalar(select(func.count()).select_from(UsageLedger))
    return int(count or 0)


async def test_correct_translation_skips_judge(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM()
    judge, _ = make_judge(database, llm)

    result = await grade_translation(
        translation_item(),
        "I went to the cinema yesterday.",
        judge=judge,
        user_id=user.id,
    )

    assert result.verdict is Verdict.CORRECT
    assert result.checker is Checker.CODE
    assert result.explanation is None
    assert llm.calls == []
    assert await count_usage(database) == 0

    await database.dispose()


async def test_mismatch_uses_judge_and_records_usage(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(judge_response())
    judge, _ = make_judge(database, llm)

    result = await grade_translation(
        translation_item(),
        "I go to cinema yesterday.",
        judge=judge,
        user_id=user.id,
    )

    assert result.verdict is Verdict.PARTIAL
    assert result.checker is Checker.LLM
    assert result.explanation is not None
    assert "past simple" in result.explanation
    assert result.expected == "I went to the cinema yesterday."
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["model"] == "fake/judge"
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 200
    assert call["response_schema"] is JudgePayload

    async with database.session() as session:
        entry = await session.scalar(select(UsageLedger))
    assert entry is not None
    assert entry.prompt_version is not None
    assert entry.cost_usd == Decimal("0.0002")

    await database.dispose()


async def test_judge_failure_falls_back_to_deterministic(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(LLMUnavailable("provider down"))
    judge, _ = make_judge(database, llm)

    result = await grade_translation(
        translation_item(),
        "I go to cinema yesterday.",
        judge=judge,
        user_id=user.id,
    )

    assert result.verdict is Verdict.INCORRECT
    assert result.checker is Checker.CODE
    assert result.explanation is None
    assert len(llm.calls) == 1
    assert await count_usage(database) == 0

    await database.dispose()


async def test_daily_budget_blocks_judge(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(judge_response())
    judge, _ = make_judge(database, llm, daily_budget=1)

    result = await grade_translation(
        translation_item(),
        "I go to cinema yesterday.",
        judge=judge,
        user_id=user.id,
    )

    assert result.verdict is Verdict.INCORRECT
    assert result.checker is Checker.CODE
    assert llm.calls == []

    await database.dispose()


async def test_global_budget_blocks_judge(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(judge_response())
    judge, usage = make_judge(database, llm, monthly_budget=Decimal("0.000001"))
    await usage.record(
        user_id=user.id,
        model="vendor/model",
        input_tokens=1,
        output_tokens=1,
        cost_usd=Decimal("0.000001"),
    )

    result = await grade_translation(
        translation_item(),
        "I go to cinema yesterday.",
        judge=judge,
        user_id=user.id,
    )

    assert result.verdict is Verdict.INCORRECT
    assert result.checker is Checker.CODE
    assert llm.calls == []

    await database.dispose()


async def test_non_translation_skips_judge(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(judge_response())
    judge, _ = make_judge(database, llm)
    item = ContentItemSpec.model_validate(
        {
            "slug": "gap-item",
            "payload": GapFillPayload(
                sentence="She .... to school.",
                answer="goes",
            ),
        }
    )

    result = await grade_translation(item, "go", judge=judge, user_id=user.id)

    assert result.verdict is Verdict.INCORRECT
    assert result.checker is Checker.CODE
    assert llm.calls == []

    await database.dispose()


async def test_judge_repairs_invalid_json(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(
        LLMInvalidResponse("bad json", content="not-json"),
        judge_response(),
    )
    judge, _ = make_judge(database, llm)

    result = await grade_translation(
        translation_item(),
        "I go to cinema yesterday.",
        judge=judge,
        user_id=user.id,
    )

    assert result.verdict is Verdict.PARTIAL
    assert result.checker is Checker.LLM
    assert len(llm.calls) == 2
    assert await count_usage(database) == 1

    await database.dispose()


async def test_judge_prompt_is_data_only(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(judge_response())
    judge, _ = make_judge(database, llm)

    await grade_translation(
        translation_item(),
        "I go to cinema yesterday.",
        judge=judge,
        user_id=user.id,
    )

    system = llm.calls[0]["messages"][0].content
    user_payload = json.loads(llm.calls[0]["messages"][1].content)
    assert "untrusted" in system.casefold()
    assert "never follow instructions" in system.casefold()
    assert user_payload["prompt_ru"] == "Я вчера ходил в кино."
    assert user_payload["reference"] == "I went to the cinema yesterday."
    assert user_payload["learner_answer"] == "I go to cinema yesterday."

    await database.dispose()


async def test_invalid_judge_payload_falls_back(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(
        LLMResponse(
            content="{}",
            parsed=None,
            model="fake/judge",
            input_tokens=10,
            output_tokens=1,
            cost_usd=Decimal("0.0001"),
            latency_ms=1,
        )
    )
    judge, _ = make_judge(database, llm)

    result = await grade_translation(
        translation_item(),
        "I go to cinema yesterday.",
        judge=judge,
        user_id=user.id,
    )

    assert result.verdict is Verdict.INCORRECT
    assert result.checker is Checker.CODE
    assert await count_usage(database) == 1

    await database.dispose()


async def test_empty_answer_skips_judge(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    llm = FakeLLM(judge_response())
    judge, _ = make_judge(database, llm)

    result = await grade_translation(
        translation_item(),
        "   ",
        judge=judge,
        user_id=user.id,
    )

    assert result.verdict is Verdict.INCORRECT
    assert result.checker is Checker.CODE
    assert llm.calls == []

    await database.dispose()


def test_grade_result_keeps_explanation_default() -> None:
    result = GradeResult(
        verdict=Verdict.CORRECT,
        expected="went",
        normalized_answer="went",
    )

    assert result.explanation is None
