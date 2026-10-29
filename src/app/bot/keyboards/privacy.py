"""Privacy keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import DeleteCB
from app.texts import ru


def delete_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=ru.BUTTON_DELETE,
                    callback_data=DeleteCB(action="confirm").pack(),
                ),
                InlineKeyboardButton(
                    text=ru.BUTTON_DELETE_CANCEL,
                    callback_data=DeleteCB(action="cancel").pack(),
                ),
            ]
        ]
    )
