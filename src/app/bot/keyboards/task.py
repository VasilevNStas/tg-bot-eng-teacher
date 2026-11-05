"""Inline controls shown directly under an active task."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import TaskCB
from app.texts import ru


def task_keyboard() -> InlineKeyboardMarkup:
    """A single exit button attached to every task question."""
    builder = InlineKeyboardBuilder()
    builder.button(text=ru.BUTTON_CANCEL_TASK, callback_data=TaskCB(action="cancel"))
    return builder.as_markup()
