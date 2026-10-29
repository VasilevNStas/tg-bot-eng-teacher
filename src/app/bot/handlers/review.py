"""Spaced-repetition review flow."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot.callbacks import ReviewCB
from app.bot.formatters import format_review_card
from app.bot.keyboards.onboarding import start_keyboard
from app.bot.keyboards.review import review_keyboard
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.services.reminders.service import ReminderService
from app.services.vocabulary.srs import ReviewGrade, ReviewNotFound, SrsService
from app.texts import ru


async def _edit(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=reply_markup)


async def handle_review(message: Message, services: AppServices) -> None:
    if message.from_user is None:
        return

    user = await resolve_user(services, message.from_user)
    if user.onboarding_state != "done":
        await message.answer(ru.ONBOARDING_REQUIRED, reply_markup=start_keyboard())
        return

    cards = await SrsService(services.database).due_cards(user.id, limit=1)
    if not cards:
        await message.answer(ru.REVIEW_EMPTY)
        return

    card = cards[0]
    await message.answer(
        format_review_card(card),
        reply_markup=review_keyboard(card.vocabulary_id),
    )


async def handle_grade(
    callback: CallbackQuery,
    callback_data: ReviewCB,
    services: AppServices,
) -> None:
    if callback.from_user is None:
        return

    user = await resolve_user(services, callback.from_user)
    if user.onboarding_state != "done":
        await callback.answer(ru.ONBOARDING_REQUIRED)
        return

    try:
        grade = ReviewGrade(callback_data.action)
    except ValueError:
        await callback.answer(ru.REVIEW_UNKNOWN)
        return

    service = SrsService(services.database)
    try:
        due_at = await service.review(user.id, callback_data.vocabulary_id, grade)
    except ReviewNotFound:
        await callback.answer(ru.REVIEW_UNKNOWN)
        return

    await ReminderService(services.database).schedule_srs(user.id, due_at)

    cards = await service.due_cards(user.id, limit=1)
    if cards:
        card = cards[0]
        await _edit(
            callback,
            format_review_card(card),
            reply_markup=review_keyboard(card.vocabulary_id),
        )
    else:
        await _edit(callback, ru.REVIEW_DONE)
    await callback.answer()


def build_router() -> Router:
    router = Router(name="review")
    router.message.register(handle_review, Command("review"))
    router.callback_query.register(handle_grade, ReviewCB.filter())
    return router


router = build_router()
