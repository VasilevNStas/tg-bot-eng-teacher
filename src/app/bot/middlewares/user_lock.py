"""Per-user serialization of update processing.

Telegram can deliver two updates for the same user close together (e.g. a
message and a button press). Without a lock, both handlers could read the same
FSM state and advance it twice. The registry keeps one ``asyncio.Lock`` per
active user and drops it when the last waiter finishes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.core.logging import get_logger

logger = get_logger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class UserLockRegistry:
    """Reference-counted map of per-user locks."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._refs: dict[int, int] = {}

    @property
    def active_locks(self) -> int:
        return len(self._locks)

    @asynccontextmanager
    async def acquire(self, user_id: int) -> AsyncIterator[None]:
        """Hold the user's lock for the duration of the context.

        The reference count prevents one finishing task from deleting a lock
        that another task is still waiting on.
        """
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock

        self._refs[user_id] = self._refs.get(user_id, 0) + 1
        try:
            async with lock:
                yield
        finally:
            remaining = self._refs[user_id] - 1
            if remaining <= 0:
                self._refs.pop(user_id, None)
                self._locks.pop(user_id, None)
            else:
                self._refs[user_id] = remaining


def extract_user_id(event: TelegramObject) -> int | None:
    """Find the acting user in the update types the bot handles."""
    if not isinstance(event, Update):
        return None
    for candidate in (event.message, event.edited_message, event.callback_query):
        if candidate is None:
            continue
        if candidate.from_user is not None:
            return candidate.from_user.id
    return None


class UserLockMiddleware(BaseMiddleware):
    """Serialize handler execution per Telegram user."""

    def __init__(self, registry: UserLockRegistry, *, timeout_seconds: float = 90.0) -> None:
        self._registry = registry
        self._timeout = timeout_seconds

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = extract_user_id(event)
        if user_id is None:
            # Updates without a user (channel posts, service updates) do not
            # need serialization.
            return await handler(event, data)

        try:
            # A stuck handler must not block this user's queue forever.
            async with asyncio.timeout(self._timeout):
                async with self._registry.acquire(user_id):
                    return await handler(event, data)
        except TimeoutError:
            logger.warning("user_lock_timeout", user_id=user_id)
            return None
