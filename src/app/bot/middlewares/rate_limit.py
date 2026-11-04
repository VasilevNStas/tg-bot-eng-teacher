"""Short-window flood control for user updates.

This is deliberately not a daily quota: the LLM budget already limits cost.
The middleware only smooths bursts so one user cannot monopolise the polling
loop or spam the LLM. It is disabled by default and toggled via env.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.bot.middlewares.user_lock import extract_user_id
from app.core.logging import get_logger
from app.texts import ru

logger = get_logger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class RateLimitMiddleware(BaseMiddleware):
    """Drop updates above a per-user messages-per-minute limit."""

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[int, deque[float]] = {}
        self._warned_at: dict[int, float] = {}

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = extract_user_id(event)
        if user_id is None:
            return await handler(event, data)

        now = self._clock()
        hits = self._hits.setdefault(user_id, deque())
        cutoff = now - self._window
        while hits and hits[0] < cutoff:
            hits.popleft()

        if len(hits) >= self._limit:
            if now - self._warned_at.get(user_id, 0.0) >= self._window:
                self._warned_at[user_id] = now
                await _warn(event)
            logger.info("rate_limited", tg_id=user_id, limit=self._limit)
            return None

        hits.append(now)
        return await handler(event, data)


async def _warn(event: TelegramObject) -> None:
    """Tell the user once per window without revealing internals."""
    from aiogram.types import Update

    if not isinstance(event, Update):
        return
    if event.callback_query is not None:
        await event.callback_query.answer(ru.RATE_LIMITED)
    elif event.message is not None:
        await event.message.answer(ru.RATE_LIMITED)
