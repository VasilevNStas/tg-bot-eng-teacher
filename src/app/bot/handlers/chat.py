"""Free conversation handler."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from app.bot.formatters import TELEGRAM_LIMIT, format_corrections, format_reply
from app.bot.handlers.errors import answer_llm_error
from app.bot.keyboards.onboarding import start_keyboard
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.db.enums import ConversationMode
from app.llm.errors import LLMError
from app.services.conversation.service import ConversationTurn
from app.services.prompts.errors import PromptTemplateNotFound, PromptTooLong
from app.services.prompts.registry import PromptTemplate
from app.services.usage.service import DailyBudgetExceeded
from app.texts import ru


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

    mode: ConversationMode = ConversationMode.CHAT
    scenario: PromptTemplate | None = None
    active_scenario = await services.conversation.active_roleplay(user.id)
    if active_scenario is not None:
        try:
            scenario = services.prompts.scenario(active_scenario)
        except PromptTemplateNotFound:
            await services.conversation.stop_roleplay(user.id)
            await message.answer(ru.ROLEPLAY_UNAVAILABLE)
            return
        mode = ConversationMode.ROLEPLAY

    try:
        turn = await services.conversation.respond(
            user,
            message.text,
            mode=mode,
            update_id=update_id,
            scenario=scenario,
        )
    except (PromptTooLong, DailyBudgetExceeded, LLMError) as exc:
        await answer_llm_error(
            message,
            exc,
            user_id=user.id,
            operation="roleplay" if scenario is not None else "chat",
        )
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
