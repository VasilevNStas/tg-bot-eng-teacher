"""Saved vocabulary list."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.formatters import format_vocabulary
from app.bot.keyboards.onboarding import start_keyboard
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.db.repositories.vocabulary import VocabularyRepository
from app.texts import ru


async def handle_vocab(message: Message, services: AppServices) -> None:
    if message.from_user is None:
        return

    user = await resolve_user(services, message.from_user)
    if user.onboarding_state != "done":
        await message.answer(ru.ONBOARDING_REQUIRED, reply_markup=start_keyboard())
        return

    async with services.database.session() as session:
        words = await VocabularyRepository(session).list_for_user(user.id, limit=50)

    if not words:
        await message.answer(ru.VOCAB_EMPTY)
        return
    await message.answer(format_vocabulary(words))


def build_router() -> Router:
    router = Router(name="vocab")
    router.message.register(handle_vocab, Command("vocab"))
    return router


router = build_router()
