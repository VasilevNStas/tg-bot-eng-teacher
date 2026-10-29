"""Timezone and quiet-hours preferences."""

from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.db.repositories.users import UserRepository
from app.services.reminders.quiet import parse_clock
from app.texts import ru

QUIET_RANGE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


def _arg(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def handle_timezone(message: Message, services: AppServices) -> None:
    if message.from_user is None:
        return
    user = await resolve_user(services, message.from_user)
    value = _arg(message)
    if not value:
        await message.answer(ru.TIMEZONE_CURRENT.format(timezone=user.timezone))
        return
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        await message.answer(ru.TIMEZONE_INVALID)
        return

    async with services.database.session() as session:
        await UserRepository(session).update_timezone(user.tg_id, value)
    await message.answer(ru.TIMEZONE_SET.format(timezone=value))


async def handle_quiet(message: Message, services: AppServices) -> None:
    if message.from_user is None:
        return
    user = await resolve_user(services, message.from_user)
    value = _arg(message).lower()
    if not value:
        if user.quiet_hours_start and user.quiet_hours_end:
            await message.answer(
                ru.QUIET_CURRENT_ON.format(
                    start=user.quiet_hours_start,
                    end=user.quiet_hours_end,
                    timezone=user.timezone,
                )
            )
        else:
            await message.answer(ru.QUIET_CURRENT_OFF)
        return
    if value == "off":
        async with services.database.session() as session:
            await UserRepository(session).update_quiet_hours(user.tg_id, None, None)
        await message.answer(ru.QUIET_OFF)
        return
    if not QUIET_RANGE.fullmatch(value):
        await message.answer(ru.QUIET_INVALID)
        return

    start, end = value.split("-", 1)
    if parse_clock(start) == parse_clock(end):
        await message.answer(ru.QUIET_INVALID)
        return

    async with services.database.session() as session:
        await UserRepository(session).update_quiet_hours(user.tg_id, start, end)
    await message.answer(ru.QUIET_SET.format(start=start, end=end, timezone=user.timezone))


def build_router() -> Router:
    router = Router(name="preferences")
    router.message.register(handle_timezone, Command("timezone"))
    router.message.register(handle_quiet, Command("quiet"))
    return router


router = build_router()
