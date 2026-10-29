"""Word card keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import WordsCB
from app.services.words.service import WordCardDirection
from app.texts import ru

CARD_SIZES = (5, 10, 15, 20)


def study_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=ru.BUTTON_START_QUIZ, callback_data=WordsCB(action="quiz"))
    builder.button(text=ru.BUTTON_SETTINGS, callback_data=WordsCB(action="settings"))
    builder.adjust(2)
    return builder.as_markup()


def settings_keyboard(size: int, direction: WordCardDirection) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for card_size in CARD_SIZES:
        builder.button(
            text=f"{'✓ ' if card_size == size else ''}{card_size}",
            callback_data=WordsCB(action="size", value=str(card_size)),
        )
    for value in WordCardDirection:
        builder.button(
            text=f"{'✓ ' if value is direction else ''}{ru.WORDS_DIRECTION_LABELS[value.value]}",
            callback_data=WordsCB(action="direction", value=value.value),
        )
    builder.button(text=ru.BUTTON_START_QUIZ, callback_data=WordsCB(action="quiz"))
    builder.adjust(4, 2, 2, 1)
    return builder.as_markup()


def result_keyboard(*, has_wrong: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_wrong:
        builder.button(
            text=ru.BUTTON_RETRY_WRONG,
            callback_data=WordsCB(action="retry"),
        )
    builder.button(text=ru.BUTTON_NEW_CARD, callback_data=WordsCB(action="new"))
    builder.adjust(1)
    return builder.as_markup()
