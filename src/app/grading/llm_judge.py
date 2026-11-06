"""LLM judge for class-B translation exercises (B-018, ADR-018).

The deterministic ladder runs first. The judge is only a second opinion when
the answer does not match the reference, and a failed judge call degrades to
the deterministic verdict instead of blocking the attempt.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.logging import get_logger
from app.curriculum.models import ContentItemSpec, TranslationPayload
from app.db.enums import Checker, Verdict
from app.grading.grader import GradeResult, grade_item
from app.grading.normalize import normalize_answer
from app.llm.errors import LLMError, LLMInvalidResponse
from app.llm.models import RouteSpec
from app.llm.port import LLMClient, LLMResponse
from app.llm.port import Message as LlmMessage
from app.llm.pricing import estimate_route_cost_usd
from app.llm.tokens import estimate_messages_tokens
from app.services.prompts.registry import PromptRegistry
from app.services.usage.service import DailyBudgetExceeded, GlobalBudgetExceeded, UsageService

logger = get_logger(__name__)

_REPAIR_INSTRUCTION = (
    "Your previous answer could not be parsed. Return ONLY one JSON object "
    'with fields "verdict" and "explanation_ru", with no prose and no code fences.'
)
_MAX_ANSWER_CHARS = 2000


class JudgePayload(BaseModel):
    """Structured verdict returned by the judge."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict = Field(description="correct, partial or incorrect")
    explanation_ru: str = Field(min_length=1, max_length=500)


class LLMJudge:
    """One-call judge for translation mismatches, with budget and fallback."""

    def __init__(
        self,
        llm: LLMClient,
        usage: UsageService,
        registry: PromptRegistry,
        route: RouteSpec,
        *,
        repair_attempts: int = 1,
    ) -> None:
        if not route.requires_json:
            raise ValueError("judge route must require JSON output")
        if repair_attempts < 0:
            raise ValueError("repair_attempts must not be negative")
        self._llm = llm
        self._usage = usage
        self._route = route
        self._template = registry.judge()
        self._repair_attempts = repair_attempts

    async def judge_translation(
        self,
        item: ContentItemSpec,
        answer: str,
        *,
        user_id: int,
    ) -> GradeResult | None:
        """Judge one translation; return ``None`` when the judge cannot help."""
        payload = item.payload
        if not isinstance(payload, TranslationPayload) or not normalize_answer(answer):
            return None

        messages = self._messages(payload, answer)
        input_tokens = estimate_messages_tokens(messages)
        try:
            await self._usage.ensure_available(
                user_id,
                estimated_tokens=input_tokens + self._route.max_tokens,
            )
            await self._usage.ensure_global_available(
                estimated_cost_usd=estimate_route_cost_usd(self._route, input_tokens)
            )
        except (DailyBudgetExceeded, GlobalBudgetExceeded) as exc:
            logger.warning(
                "judge_skipped",
                item=item.slug,
                reason="budget",
                error=type(exc).__name__,
            )
            return None

        try:
            response = await self._complete(messages, item_slug=item.slug)
        except LLMError as exc:
            logger.warning(
                "judge_failed",
                item=item.slug,
                error=type(exc).__name__,
            )
            return None

        await self._record_usage(response, user_id=user_id)
        try:
            judged = JudgePayload.model_validate(response.parsed)
        except ValidationError as exc:
            logger.warning(
                "judge_invalid_payload",
                item=item.slug,
                error=str(exc),
            )
            return None

        return GradeResult(
            verdict=judged.verdict,
            expected=payload.reference,
            normalized_answer=normalize_answer(answer),
            matched=payload.reference,
            checker=Checker.LLM,
            explanation=judged.explanation_ru,
        )

    def _messages(self, payload: TranslationPayload, answer: str) -> list[LlmMessage]:
        data = {
            "prompt_ru": payload.prompt_ru,
            "reference": payload.reference,
            "accepted": list(payload.accepted),
            "learner_answer": answer[:_MAX_ANSWER_CHARS],
        }
        return [
            LlmMessage(role="system", content=self._template.content),
            LlmMessage(
                role="user",
                content=json.dumps(data, ensure_ascii=False, sort_keys=True),
            ),
        ]

    async def _complete(
        self,
        messages: list[LlmMessage],
        *,
        item_slug: str,
    ) -> LLMResponse:
        """Call the judge route and try one structured-output repair."""
        try:
            return await self._call(messages)
        except LLMInvalidResponse as exc:
            if self._repair_attempts < 1:
                raise
            logger.warning("judge_repair", item=item_slug)
            repair = list(messages)
            if exc.content:
                repair.append(LlmMessage(role="assistant", content=exc.content))
            repair.append(LlmMessage(role="user", content=_REPAIR_INSTRUCTION))
            return await self._call(repair)

    async def _call(self, messages: list[LlmMessage]) -> LLMResponse:
        response = await self._llm.complete(
            messages,
            model=self._route.model,
            fallback_model=self._route.fallback,
            temperature=self._route.temperature,
            max_tokens=self._route.max_tokens,
            response_schema=JudgePayload,
        )
        logger.info(
            "llm_call",
            route="judge",
            model=response.model,
            prompt_version=self._template.version,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=str(response.cost_usd),
            latency_ms=response.latency_ms,
        )
        return response

    async def _record_usage(self, response: LLMResponse, *, user_id: int) -> None:
        await self._usage.record(
            user_id=user_id,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=response.cost_usd,
            prompt_version=self._template.version,
            latency_ms=response.latency_ms,
        )


async def grade_translation(
    item: ContentItemSpec,
    answer: str,
    *,
    judge: LLMJudge | None,
    user_id: int,
) -> GradeResult:
    """Grade a class-B translation: deterministic first, judge on mismatch."""
    deterministic = grade_item(item, answer)
    if deterministic.verdict is Verdict.CORRECT or judge is None:
        return deterministic
    if not isinstance(item.payload, TranslationPayload):
        return deterministic
    judged = await judge.judge_translation(item, answer, user_id=user_id)
    return judged or deterministic
