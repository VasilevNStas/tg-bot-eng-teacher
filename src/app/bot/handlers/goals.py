"""Personal learning goals: ``/goals`` and inline multi-select.

The selected values live in ``users.profile["goals"]`` and are read by the
prompt builder and by ``/drill``. Each toggle writes the whole section, so the
UI survives restarts and never depends on FSM state.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot.callbacks import GoalsCB
from app.bot.keyboards.goals import (
    daily_keyboard,
    done_keyboard,
    goals_keyboard,
    interests_keyboard,
)
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.services.goals.model import GoalsProfile
from app.services.goals.service import GoalsService
from app.texts import ru

_SCREENS = {"goals", "interests", "daily"}


def _labels(ids: tuple[str, ...], labels: dict[str, str]) -> str:
    """Join selected labels in their canonical order."""
    return ", ".join(labels[item] for item in ids) if ids else ru.GOALS_NONE


def _render(profile: GoalsProfile, screen: str) -> tuple[str, InlineKeyboardMarkup]:
    """Build the text and keyboard for one editor screen."""
    if screen == "interests":
        return (
            ru.GOALS_INTERESTS_TEXT.format(
                interests=_labels(profile.interests, ru.INTEREST_LABELS)
            ),
            interests_keyboard(profile),
        )
    if screen == "daily":
        return (
            ru.GOALS_DAILY_TEXT.format(daily=profile.effective_daily_items),
            daily_keyboard(profile),
        )
    return (
        ru.GOALS_GOALS_TEXT.format(goals=_labels(profile.goals, ru.GOAL_LABELS)),
        goals_keyboard(profile),
    )


def _summary(profile: GoalsProfile) -> str:
    """Render the confirmation screen after the user taps "Готово"."""
    return ru.GOALS_SUMMARY.format(
        goals=_labels(profile.goals, ru.GOAL_LABELS),
        interests=_labels(profile.interests, ru.INTEREST_LABELS),
        daily=profile.effective_daily_items,
    )


async def handle_goals(message: Message, services: AppServices) -> None:
    """``/goals``: open the goals editor."""
    if message.from_user is None:
        return

    user = await resolve_user(services, message.from_user)
    profile = GoalsService.read(user)
    text, keyboard = _render(profile, "goals")
    await message.answer(text, reply_markup=keyboard)


async def handle_goals_callback(
    callback: CallbackQuery,
    callback_data: GoalsCB,
    services: AppServices,
) -> None:
    """Toggle a goal/interest, change the daily target, or navigate."""
    if callback.from_user is None:
        return

    user = await resolve_user(services, callback.from_user)
    service = GoalsService(services.database)
    profile = service.read(user)
    action = callback_data.action
    screen = "goals"
    changed = False

    if action == "toggle_goal":
        profile = profile.toggle_goal(callback_data.value)
        changed = True
    elif action == "toggle_interest":
        profile = profile.toggle_interest(callback_data.value)
        screen = "interests"
        changed = True
    elif action == "daily":
        try:
            daily = int(callback_data.value)
        except ValueError:
            await callback.answer(ru.GOALS_INVALID)
            return
        profile = profile.with_daily_items(daily)
        screen = "daily"
        changed = True
    elif action == "reset":
        profile = profile.cleared()
        changed = True
    elif action == "screen":
        screen = callback_data.value if callback_data.value in _SCREENS else "goals"
    elif action == "done":
        screen = "done"

    if changed:
        await service.save(user, profile)

    if screen == "done":
        text, keyboard = _summary(profile), done_keyboard()
    else:
        text, keyboard = _render(profile, screen)

    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


def build_router() -> Router:
    """Wire ``/goals`` and its inline callbacks."""
    router = Router(name="goals")
    router.message.register(handle_goals, Command("goals"))
    router.callback_query.register(handle_goals_callback, GoalsCB.filter())
    return router


router = build_router()
