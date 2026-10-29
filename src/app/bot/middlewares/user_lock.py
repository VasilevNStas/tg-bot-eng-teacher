"""Per-user serialization of update processing."""

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
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._refs: dict[int, int] = {}

    @property
    def active_locks(self) -> int:
        return len(self._locks)

    @asynccontextmanager
    async def acquire(self, user_id: int) -> AsyncIterator[None]:
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
    if not isinstance(event, Update):
        return None
    for candidate in (event.message, event.edited_message, event.callback_query):
        if candidate is None:
            continue
        if candidate.from_user is not None:
            return candidate.from_user.id
    return None


class UserLockMiddleware(BaseMiddleware):
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
            return await handler(event, data)

        try:
            async with asyncio.timeout(self._timeout):
                async with self._registry.acquire(user_id):
                    return await handler(event, data)
        except TimeoutError:
            logger.warning("user_lock_timeout", user_id=user_id)
            return None
