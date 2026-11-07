"""Role-play scenario selection and lifecycle.

``/roleplay`` either starts the scenario passed as an argument or shows the
scenario keyboard. Scenario metadata lives in prompt templates; the actual
dialogue is owned by ``ConversationService``.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import RoleplayCB, RoleplayReviewCB
from app.bot.formatters import format_roleplay_review
from app.bot.keyboards.onboarding import start_keyboard
from app.bot.keyboards.roleplay import review_keyboard, scenarios_keyboard
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.services.prompts.errors import PromptTemplateNotFound
from app.texts import ru


def _scenario_arg(message: Message) -> str:
    """Return the lower-cased argument after ``/roleplay`` or an empty string."""
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip().lower() if len(parts) > 1 else ""


async def _start_scenario(message: Message, services: AppServices, slug: str) -> None:
    """Validate the slug and switch the user's conversation into role-play."""
    if message.from_user is None:
        return
    user = await resolve_user(services, message.from_user)
    if user.onboarding_state != "done":
        await message.answer(ru.ONBOARDING_REQUIRED, reply_markup=start_keyboard())
        return
    try:
        scenario = services.prompts.scenario(slug)
    except PromptTemplateNotFound:
        await message.answer(ru.ROLEPLAY_UNKNOWN)
        return
    await services.conversation.start_roleplay(user.id, scenario.id)
    await message.answer(ru.ROLEPLAY_STARTED.format(title=scenario.title or scenario.id))


async def handle_roleplay(message: Message, services: AppServices) -> None:
    """Start a named scenario or present the available scenarios."""
    slug = _scenario_arg(message)
    if slug:
        await _start_scenario(message, services, slug)
        return
    if message.from_user is None:
        return

    user = await resolve_user(services, message.from_user)
    if user.onboarding_state != "done":
        await message.answer(ru.ONBOARDING_REQUIRED, reply_markup=start_keyboard())
        return

    scenarios = services.prompts.scenarios()
    if not scenarios:
        await message.answer(ru.ROLEPLAY_UNAVAILABLE)
        return
    await message.answer(ru.ROLEPLAY_CHOOSE, reply_markup=scenarios_keyboard(scenarios))


async def handle_start(
    callback: CallbackQuery,
    callback_data: RoleplayCB,
    services: AppServices,
) -> None:
    """Handle a scenario chosen from the inline keyboard."""
    if callback.from_user is None:
        return
    user = await resolve_user(services, callback.from_user)
    if user.onboarding_state != "done":
        await callback.answer(ru.ONBOARDING_REQUIRED)
        return
    try:
        scenario = services.prompts.scenario(callback_data.slug)
    except PromptTemplateNotFound:
        await callback.answer(ru.ROLEPLAY_UNKNOWN)
        return
    await services.conversation.start_roleplay(user.id, scenario.id)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            ru.ROLEPLAY_STARTED.format(title=scenario.title or scenario.id)
        )
    await callback.answer()


async def handle_stop(message: Message, services: AppServices) -> None:
    """End the active role-play and offer the full review when useful."""
    if message.from_user is None:
        return
    user = await resolve_user(services, message.from_user)
    session = await services.conversation.stop_roleplay(user.id)
    if session is None:
        await message.answer(ru.ROLEPLAY_NONE)
        return
    if session.corrections:
        await message.answer(
            ru.ROLEPLAY_STOPPED,
            reply_markup=review_keyboard(session.conversation_id),
        )
        return
    await message.answer(ru.ROLEPLAY_STOPPED)


async def handle_roleplay_review(
    callback: CallbackQuery,
    callback_data: RoleplayReviewCB,
    services: AppServices,
) -> None:
    """Show every correction collected during one role-play scene."""
    if callback.from_user is None:
        return
    user = await resolve_user(services, callback.from_user)
    review = await services.conversation.roleplay_review(
        user.id,
        callback_data.conversation_id,
    )
    if review is None:
        await callback.answer(ru.ROLEPLAY_REVIEW_UNAVAILABLE)
        return

    title = review.topic
    if review.topic is not None:
        try:
            title = services.prompts.scenario(review.topic).title or review.topic
        except PromptTemplateNotFound:
            title = review.topic

    if isinstance(callback.message, Message):
        await callback.message.edit_text(format_roleplay_review(review, title=title))
    await callback.answer()


def build_router() -> Router:
    """Wire role-play commands and scenario callbacks."""
    router = Router(name="roleplay")
    router.message.register(handle_roleplay, Command("roleplay"))
    router.message.register(handle_stop, Command("stop"))
    router.callback_query.register(handle_start, RoleplayCB.filter())
    router.callback_query.register(handle_roleplay_review, RoleplayReviewCB.filter())
    return router


router = build_router()
