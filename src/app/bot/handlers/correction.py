"""Explicit correction mode."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.formatters import format_correction_report
from app.bot.handlers.errors import answer_llm_error
from app.bot.keyboards.onboarding import start_keyboard
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.db.enums import ConversationMode
from app.llm.errors import LLMError
from app.services.prompts.errors import PromptTooLong
from app.services.usage.service import DailyBudgetExceeded
from app.texts import ru


def _correction_text(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def handle_correction(
    message: Message,
    services: AppServices,
    update_id: int | None = None,
) -> None:
    if message.from_user is None:
        return

    text = _correction_text(message)
    if not text:
        await message.answer(ru.CORRECTION_USAGE)
        return

    user = await resolve_user(services, message.from_user)
    if user.onboarding_state != "done":
        await message.answer(ru.ONBOARDING_REQUIRED, reply_markup=start_keyboard())
        return

    try:
        turn = await services.conversation.respond(
            user,
            text,
            mode=ConversationMode.CORRECTION,
            update_id=update_id,
        )
    except (PromptTooLong, DailyBudgetExceeded, LLMError) as exc:
        await answer_llm_error(message, exc, user_id=user.id, operation="correction")
        return

    await message.answer(format_correction_report(turn))


def build_router() -> Router:
    router = Router(name="correction")
    router.message.register(handle_correction, Command("correction"))
    return router


router = build_router()
