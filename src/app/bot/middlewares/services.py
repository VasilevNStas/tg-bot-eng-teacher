"""Injects the application service container into handler data."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.core.di import AppServices

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class ServicesMiddleware(BaseMiddleware):
    def __init__(self, services: AppServices) -> None:
        self._services = services

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["services"] = self._services
        return await handler(event, data)
