"""Role-play scenario selection and lifecycle."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import RoleplayCB
from app.bot.keyboards.onboarding import start_keyboard
from app.bot.keyboards.roleplay import scenarios_keyboard
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.services.prompts.errors import PromptTemplateNotFound
from app.texts import ru


def _scenario_arg(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip().lower() if len(parts) > 1 else ""


async def _start_scenario(message: Message, services: AppServices, slug: str) -> None:
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
    if message.from_user is None:
        return
    user = await resolve_user(services, message.from_user)
    stopped = await services.conversation.stop_roleplay(user.id)
    await message.answer(ru.ROLEPLAY_STOPPED if stopped else ru.ROLEPLAY_NONE)


def build_router() -> Router:
    router = Router(name="roleplay")
    router.message.register(handle_roleplay, Command("roleplay"))
    router.message.register(handle_stop, Command("stop"))
    router.callback_query.register(handle_start, RoleplayCB.filter())
    return router


router = build_router()
