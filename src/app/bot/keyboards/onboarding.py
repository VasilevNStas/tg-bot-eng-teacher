"""Onboarding keyboards."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import OnboardingCB
from app.db.enums import CEFRLevel
from app.texts import ru


def start_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=ru.BUTTON_TAKE_TEST, callback_data=OnboardingCB(action="test"))
    builder.button(text=ru.BUTTON_SKIP, callback_data=OnboardingCB(action="skip"))
    builder.adjust(1)
    return builder.as_markup()


def question_keyboard(index: int, options: Sequence[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for option_index, option in enumerate(options):
        builder.button(
            text=option,
            callback_data=OnboardingCB(
                action="answer",
                index=index,
                value=str(option_index),
            ),
        )
    builder.adjust(1)
    return builder.as_markup()


def levels_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for level in CEFRLevel:
        builder.button(
            text=level.value,
            callback_data=OnboardingCB(action="level", value=level.value),
        )
    builder.adjust(3)
    return builder.as_markup()


def result_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=ru.BUTTON_CHANGE_LEVEL, callback_data=OnboardingCB(action="skip"))
    return builder.as_markup()
