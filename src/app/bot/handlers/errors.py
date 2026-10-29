"""Shared user-facing handling for conversation failures."""

from __future__ import annotations

from aiogram.types import Message

from app.core.logging import get_logger
from app.llm.errors import (
    LLMBalanceExhausted,
    LLMError,
    LLMInvalidResponse,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
)
from app.services.prompts.errors import PromptTooLong
from app.services.usage.service import DailyBudgetExceeded
from app.texts import ru

logger = get_logger(__name__)


async def answer_llm_error(
    message: Message,
    exc: Exception,
    *,
    user_id: int,
    operation: str,
) -> None:
    if isinstance(exc, PromptTooLong):
        logger.info(f"{operation}_prompt_too_long", user_id=user_id)
        await message.answer(ru.MESSAGE_TOO_LONG)
    elif isinstance(exc, DailyBudgetExceeded):
        logger.info(f"{operation}_budget_exhausted", user_id=user_id)
        await message.answer(ru.BUDGET_EXHAUSTED)
    elif isinstance(exc, LLMBalanceExhausted):
        logger.error("llm_balance_exhausted")
        await message.answer(ru.LLM_UNAVAILABLE)
    elif isinstance(exc, (LLMTimeout, LLMRateLimited, LLMUnavailable)):
        logger.warning("llm_temporary_failure", error=type(exc).__name__)
        await message.answer(ru.LLM_TEMPORARY_ERROR)
    elif isinstance(exc, LLMInvalidResponse):
        logger.warning("llm_invalid_response")
        await message.answer(ru.LLM_INVALID_RESPONSE)
    elif isinstance(exc, LLMError):
        logger.error("llm_failure", error=type(exc).__name__)
        await message.answer(ru.LLM_UNAVAILABLE)
    else:
        raise exc
