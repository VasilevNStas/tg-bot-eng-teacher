from __future__ import annotations

from typing import Any, cast

from aiogram.types import TelegramObject, Update

from app.bot.middlewares.services import ServicesMiddleware
from app.core.di import AppServices


async def test_injects_services_into_handler_data() -> None:
    services = cast(AppServices, object())
    middleware = ServicesMiddleware(services)
    seen: dict[str, Any] = {}

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        seen.update(data)
        return "handled"

    result = await middleware(handler, Update(update_id=1), {})

    assert result == "handled"
    assert seen["services"] is services
