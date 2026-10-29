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
    if message.from_user is None:
        await message.answer(ru.START)
        return

    user = await resolve_user(services, message.from_user)
    if user.onboarding_state == "done":
        await message.answer(ru.START)
        return

    await message.answer(ru.ONBOARDING_INTRO, reply_markup=start_keyboard())


async def handle_help(message: Message) -> None:
    await message.answer(ru.HELP)


def build_router() -> Router:
    router = Router(name="common")
    router.message.register(handle_start, CommandStart())
    router.message.register(handle_help, Command("help"))
    return router


router = build_router()
