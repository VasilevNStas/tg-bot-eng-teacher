"""Role-play scenario keyboard."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import RoleplayCB
from app.services.prompts.registry import PromptTemplate


def scenarios_keyboard(scenarios: Sequence[PromptTemplate]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for scenario in scenarios:
        builder.button(
            text=scenario.title or scenario.id,
            callback_data=RoleplayCB(action="start", slug=scenario.id),
        )
    builder.adjust(1)
    return builder.as_markup()
