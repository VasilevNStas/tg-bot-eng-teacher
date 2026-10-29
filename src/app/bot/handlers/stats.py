"""User progress command."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.formatters import format_stats
from app.bot.keyboards.onboarding import start_keyboard
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.services.stats.service import StatsService
from app.texts import ru


async def handle_stats(message: Message, services: AppServices) -> None:
    if message.from_user is None:
        return

    user = await resolve_user(services, message.from_user)
    if user.onboarding_state != "done":
        await message.answer(ru.ONBOARDING_REQUIRED, reply_markup=start_keyboard())
        return

    report = await StatsService(services.database).overview(user.id)
    await message.answer(format_stats(report))


def build_router() -> Router:
    router = Router(name="stats")
    router.message.register(handle_stats, Command("stats"))
    return router


router = build_router()
