from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
import structlog
from aiogram.types import Chat, Message, TelegramObject, Update, User

from app.bot.middlewares.observability import ObservabilityMiddleware
from app.core.metrics import MetricsRegistry


def make_update(update_id: int = 7, *, user_id: int = 100) -> Update:
    now = datetime.now(UTC)
    user = User(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=user_id, type="private")
    message = Message(
        message_id=update_id,
        date=now,
        chat=chat,
        from_user=user,
        text="hello",
    )
    return Update(update_id=update_id, message=message)


async def test_binds_context_records_success_and_clears() -> None:
    metrics = MetricsRegistry()
    middleware = ObservabilityMiddleware(metrics)
    seen: dict[str, Any] = {}

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        seen.update(structlog.contextvars.get_contextvars())
        return "handled"

    result = await middleware(handler, make_update(), {})

    assert result == "handled"
    assert seen["correlation_id"] == "tg-7"
    assert seen["update_id"] == 7
    assert seen["user_id"] == 100
    assert seen["update_type"] == "message"
    assert structlog.contextvars.get_contextvars() == {}

    snapshot = metrics.snapshot()
    assert snapshot["counters"] == {"updates_total": 1.0, "updates_ok_total": 1.0}
    timers = snapshot["timers"]
    assert isinstance(timers, dict)
    assert timers["update_duration_ms"]["count"] == 1


async def test_records_failure_and_reraises() -> None:
    metrics = MetricsRegistry()
    middleware = ObservabilityMiddleware(metrics)

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        raise RuntimeError("boom")

    with (
        patch("app.bot.middlewares.observability.sentry_sdk.capture_exception") as capture,
        pytest.raises(RuntimeError, match="boom"),
    ):
        await middleware(handler, make_update(8), {})

    capture.assert_called_once()
    snapshot = metrics.snapshot()
    assert snapshot["counters"] == {
        "updates_total": 1.0,
        "updates_failed_total": 1.0,
    }
    assert structlog.contextvars.get_contextvars() == {}


async def test_non_update_events_pass_through() -> None:
    metrics = MetricsRegistry()
    middleware = ObservabilityMiddleware(metrics)
    calls = 0

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await middleware(handler, TelegramObject(), {}) == "ok"
    assert calls == 1
    assert metrics.snapshot()["counters"] == {}
