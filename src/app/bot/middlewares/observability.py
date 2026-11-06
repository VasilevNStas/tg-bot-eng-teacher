"""Correlation context and request-level metrics for every Telegram update."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import sentry_sdk
import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.bot.middlewares.user_lock import extract_user_id
from app.core.logging import get_logger
from app.core.metrics import MetricsRegistry

logger = get_logger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

_UPDATE_FIELDS = (
    "message",
    "edited_message",
    "callback_query",
    "inline_query",
    "channel_post",
    "edited_channel_post",
    "pre_checkout_query",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
)


class ObservabilityMiddleware(BaseMiddleware):
    """Bind correlation ids and record update duration/outcome metrics."""

    def __init__(self, metrics: MetricsRegistry) -> None:
        self._metrics = metrics

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        update_id = event.update_id
        update_type = _update_type(event)
        user_id = extract_user_id(event)
        correlation_id = f"tg-{update_id}"

        structlog.contextvars.clear_contextvars()
        context: dict[str, Any] = {
            "correlation_id": correlation_id,
            "update_id": update_id,
            "update_type": update_type,
        }
        if user_id is not None:
            context["user_id"] = user_id
        structlog.contextvars.bind_contextvars(**context)
        data["correlation_id"] = correlation_id
        data["update_id"] = update_id

        self._metrics.incr("updates_total")
        started = time.perf_counter()
        try:
            result = await handler(event, data)
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            self._metrics.incr("updates_failed_total")
            self._metrics.observe("update_duration_ms", duration_ms)
            sentry_sdk.capture_exception(exc)
            logger.exception("update_failed", duration_ms=round(duration_ms, 3))
            raise
        else:
            duration_ms = (time.perf_counter() - started) * 1000
            self._metrics.incr("updates_ok_total")
            self._metrics.observe("update_duration_ms", duration_ms)
            logger.info("update_finished", duration_ms=round(duration_ms, 3))
            return result
        finally:
            structlog.contextvars.clear_contextvars()


def _update_type(event: Update) -> str:
    for name in _UPDATE_FIELDS:
        if getattr(event, name, None) is not None:
            return name
    return "other"
