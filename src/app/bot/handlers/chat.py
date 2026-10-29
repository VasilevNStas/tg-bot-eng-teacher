"""Free conversation handler."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from app.bot.formatters import TELEGRAM_LIMIT, format_corrections, format_reply
from app.bot.keyboards.onboarding import start_keyboard
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.core.logging import get_logger
from app.db.enums import ConversationMode
from app.llm.errors import (
    LLMBalanceExhausted,
    LLMError,
    LLMInvalidResponse,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
)
from app.services.conversation.service import ConversationTurn
from app.services.prompts.errors import PromptTooLong
from app.services.usage.service import DailyBudgetExceeded
from app.texts import ru

logger = get_logger(__name__)


async def handle_chat(
    message: Message,
    services: AppServices,
    update_id: int | None = None,
) -> None:
    if message.text is None or message.from_user is None:
        return

    user = await resolve_user(services, message.from_user)
    if user.onboarding_state != "done":
        await message.answer(ru.ONBOARDING_REQUIRED, reply_markup=start_keyboard())
        return

    try:
        turn = await services.conversation.respond(
            user,
            message.text,
            mode=ConversationMode.CHAT,
            update_id=update_id,
        )
    except PromptTooLong:
        logger.info("chat_prompt_too_long", user_id=user.id)
        await message.answer(ru.MESSAGE_TOO_LONG)
        return
    except DailyBudgetExceeded:
        logger.info("chat_budget_exhausted", user_id=user.id)
        await message.answer(ru.BUDGET_EXHAUSTED)
        return
    except LLMBalanceExhausted:
        logger.error("llm_balance_exhausted")
        await message.answer(ru.LLM_UNAVAILABLE)
        return
    except (LLMTimeout, LLMRateLimited, LLMUnavailable) as exc:
        logger.warning("llm_temporary_failure", error=type(exc).__name__)
        await message.answer(ru.LLM_TEMPORARY_ERROR)
        return
    except LLMInvalidResponse:
        logger.warning("llm_invalid_response")
        await message.answer(ru.LLM_INVALID_RESPONSE)
        return
    except LLMError as exc:
        logger.error("llm_failure", error=type(exc).__name__)
        await message.answer(ru.LLM_UNAVAILABLE)
        return

    await _send_turn(message, turn)


def build_router() -> Router:
    router = Router(name="chat")
    router.message.register(handle_chat, F.text & ~F.text.startswith("/"))
    return router


router = build_router()


async def _send_turn(message: Message, turn: ConversationTurn) -> None:
    reply = format_reply(turn)
    corrections = format_corrections(turn)

    if corrections is None:
        await message.answer(reply)
        return

    combined = f"{reply}\n\n{corrections}"
    if len(combined) <= TELEGRAM_LIMIT:
        await message.answer(combined)
        return

    await message.answer(reply)
    await message.answer(corrections)
