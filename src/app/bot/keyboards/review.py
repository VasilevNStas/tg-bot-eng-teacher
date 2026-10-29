"""SRS review keyboard."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import ReviewCB
from app.texts import ru


def review_keyboard(vocabulary_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=ru.BUTTON_AGAIN,
        callback_data=ReviewCB(action="again", vocabulary_id=vocabulary_id),
    )
    builder.button(
        text=ru.BUTTON_GOOD,
        callback_data=ReviewCB(action="good", vocabulary_id=vocabulary_id),
    )
    builder.button(
        text=ru.BUTTON_EASY,
        callback_data=ReviewCB(action="easy", vocabulary_id=vocabulary_id),
    )
    builder.adjust(3)
    return builder.as_markup()
