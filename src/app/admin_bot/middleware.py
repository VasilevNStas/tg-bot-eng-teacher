"""Access control for the separate admin bot.

Security comes from a numeric allowlist, not from a hard-to-guess username.
Non-allowlisted users are ignored silently so the bot reveals nothing about
itself, and only private chats are accepted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from app.admin_bot.handlers import AdminContext
from app.bot.middlewares.user_lock import extract_user_id
from app.core.admin_config import AdminSettings
from app.core.logging import get_logger

logger = get_logger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class AdminAccessMiddleware(BaseMiddleware):
    """Allow only configured numeric ids in private chats."""

    def __init__(self, settings: AdminSettings) -> None:
        self._admins = settings.admin_id_set

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = extract_user_id(event)
        if user_id is None or user_id not in self._admins:
            if user_id is not None:
                logger.warning("admin_access_denied", tg_id=user_id)
            return None
        if not _is_private(event):
            return None
        return await handler(event, data)


def _is_private(event: TelegramObject) -> bool:
    """Admin commands are accepted only in a one-to-one chat."""
    if isinstance(event, Update):
        if isinstance(event.message, Message):
            return event.message.chat.type == "private"
        if isinstance(event.callback_query, CallbackQuery):
            return (
                event.callback_query.message is not None
                and event.callback_query.message.chat.type == "private"
            )
        return False
    if isinstance(event, Message):
        return event.chat.type == "private"
    return False


class AdminContextMiddleware(BaseMiddleware):
    """Inject the admin service, settings and actor id into handlers."""

    def __init__(self, context: AdminContext) -> None:
        self._context = context

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["admin_context"] = self._context
        data["admin_settings"] = self._context.settings
        return await handler(event, data)
