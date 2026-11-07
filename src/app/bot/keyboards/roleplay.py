"""Role-play scenario keyboard."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import RoleplayCB, RoleplayReviewCB
from app.services.prompts.registry import PromptTemplate
from app.texts import ru


def scenarios_keyboard(scenarios: Sequence[PromptTemplate]) -> InlineKeyboardMarkup:
    """One button per available role-play scenario."""
    builder = InlineKeyboardBuilder()
    for scenario in scenarios:
        builder.button(
            text=scenario.title or scenario.id,
            callback_data=RoleplayCB(action="start", slug=scenario.id),
        )
    builder.adjust(1)
    return builder.as_markup()


def review_keyboard(conversation_id: int) -> InlineKeyboardMarkup:
    """Offer the full review after a scene that collected corrections."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=ru.BUTTON_ROLEPLAY_REVIEW,
        callback_data=RoleplayReviewCB(conversation_id=conversation_id),
    )
    builder.adjust(1)
    return builder.as_markup()
