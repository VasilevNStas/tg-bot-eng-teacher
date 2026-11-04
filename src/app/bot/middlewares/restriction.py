"""Enforce admin blocks and mutes in the user-facing bot.

The admin bot writes restriction state to the shared database; this middleware
reads it on every update, before any handler runs. Blocked users are dropped
silently by default, muted users get a neutral reply. The real reason stays in
the admin audit trail and is never shown to the user.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.bot.middlewares.user_lock import extract_user_id
from app.core.config import RestrictionMode, Settings
from app.core.logging import get_logger
from app.services.admin.service import AdminService
from app.texts import ru

logger = get_logger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

_NOTICE = {
    RestrictionMode.NEUTRAL: ru.RESTRICTED_NEUTRAL,
    RestrictionMode.CONFUSED: ru.RESTRICTED_CONFUSED,
    RestrictionMode.EXPLICIT: ru.RESTRICTED_EXPLICIT,
}


class RestrictionMiddleware(BaseMiddleware):
    """Stop updates from blocked or muted users."""

    def __init__(self, admin: AdminService, settings: Settings) -> None:
        self._admin = admin
        self._settings = settings

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = extract_user_id(event)
        if user_id is None:
            return await handler(event, data)

        restriction = await self._admin.restriction(user_id)
        if restriction is None:
            return await handler(event, data)

        mode = self._settings.block_mode if restriction.blocked else self._settings.mute_mode
        logger.info(
            "user_restricted",
            tg_id=user_id,
            blocked=restriction.blocked,
            muted=restriction.muted,
        )
        await _notify(event, mode)
        return None


async def _notify(event: TelegramObject, mode: RestrictionMode) -> None:
    """Send the configured reply, or nothing at all in silent mode."""
    if not isinstance(event, Update):
        return

    text = _NOTICE.get(mode)
    if event.callback_query is not None:
        await event.callback_query.answer(text or None)
        return
    if text is not None and event.message is not None:
        await event.message.answer(text)
