"""Basic commands: /start and /help."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot.keyboards.onboarding import start_keyboard
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.texts import ru


async def handle_start(message: Message, services: AppServices) -> None:
    """Greet returning users and funnel new ones into onboarding."""
    if message.from_user is None:
        await message.answer(ru.START)
        return

    user = await resolve_user(services, message.from_user)
    if user.onboarding_state == "done":
        await message.answer(ru.START)
        return

    await message.answer(ru.ONBOARDING_INTRO, reply_markup=start_keyboard())


async def handle_help(message: Message) -> None:
    """Send the static command reference."""
    await message.answer(ru.HELP)


async def handle_new(message: Message, services: AppServices) -> None:
    """Close every active conversation and start fresh on the next message."""
    if message.from_user is None:
        return

    user = await resolve_user(services, message.from_user)
    if user.onboarding_state != "done":
        await message.answer(ru.ONBOARDING_REQUIRED, reply_markup=start_keyboard())
        return

    result = await services.conversation.start_new(user.id)
    if result.closed == 0:
        await message.answer(ru.NEW_NOTHING)
        return
    await message.answer(ru.NEW_CONVERSATION)


def build_router() -> Router:
    """Wire /start, /help and /new."""
    router = Router(name="common")
    router.message.register(handle_start, CommandStart())
    router.message.register(handle_help, Command("help"))
    router.message.register(handle_new, Command("new"))
    return router


router = build_router()
