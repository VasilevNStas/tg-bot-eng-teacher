"""Inline keyboards for personal learning goals."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import GoalsCB
from app.services.goals.model import DAILY_OPTIONS, GOAL_OPTIONS, INTEREST_OPTIONS, GoalsProfile
from app.texts import ru


def _mark(selected: bool) -> str:
    """Prefix a label with a check mark when the option is selected."""
    return "✅ " if selected else "▫️ "


def _rows(count: int, *, per_row: int) -> list[int]:
    """Split ``count`` buttons into rows, keeping a trailing short row."""
    rows = [per_row] * (count // per_row)
    if count % per_row:
        rows.append(count % per_row)
    return rows


def _nav(builder: InlineKeyboardBuilder) -> None:
    """Append the shared screen navigation row(s)."""
    builder.button(
        text=ru.BUTTON_GOALS_SCREEN_GOALS,
        callback_data=GoalsCB(action="screen", value="goals"),
    )
    builder.button(
        text=ru.BUTTON_GOALS_SCREEN_INTERESTS,
        callback_data=GoalsCB(action="screen", value="interests"),
    )
    builder.button(
        text=ru.BUTTON_GOALS_SCREEN_DAILY,
        callback_data=GoalsCB(action="screen", value="daily"),
    )
    builder.button(text=ru.BUTTON_GOALS_DONE, callback_data=GoalsCB(action="done"))
    builder.button(text=ru.BUTTON_GOALS_RESET, callback_data=GoalsCB(action="reset"))


def goals_keyboard(profile: GoalsProfile) -> InlineKeyboardMarkup:
    """Multi-select list of learning goals."""
    builder = InlineKeyboardBuilder()
    for goal in GOAL_OPTIONS:
        builder.button(
            text=_mark(goal in profile.goals) + ru.GOAL_LABELS[goal],
            callback_data=GoalsCB(action="toggle_goal", value=goal),
        )
    _nav(builder)
    builder.adjust(*_rows(len(GOAL_OPTIONS), per_row=2), 3, 2)
    return builder.as_markup()


def interests_keyboard(profile: GoalsProfile) -> InlineKeyboardMarkup:
    """Multi-select list of interests used for examples and dialogues."""
    builder = InlineKeyboardBuilder()
    for interest in INTEREST_OPTIONS:
        builder.button(
            text=_mark(interest in profile.interests) + ru.INTEREST_LABELS[interest],
            callback_data=GoalsCB(action="toggle_interest", value=interest),
        )
    _nav(builder)
    builder.adjust(*_rows(len(INTEREST_OPTIONS), per_row=2), 3, 2)
    return builder.as_markup()


def daily_keyboard(profile: GoalsProfile) -> InlineKeyboardMarkup:
    """Pick the number of exercises per practice session."""
    builder = InlineKeyboardBuilder()
    for count in DAILY_OPTIONS:
        builder.button(
            text=_mark(count == profile.effective_daily_items) + str(count),
            callback_data=GoalsCB(action="daily", value=str(count)),
        )
    _nav(builder)
    builder.adjust(len(DAILY_OPTIONS), 3, 2)
    return builder.as_markup()


def done_keyboard() -> InlineKeyboardMarkup:
    """Reopen the editor after the summary screen."""
    builder = InlineKeyboardBuilder()
    builder.button(text=ru.BUTTON_GOALS_EDIT, callback_data=GoalsCB(action="open"))
    return builder.as_markup()
