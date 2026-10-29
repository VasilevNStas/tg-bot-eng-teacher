"""Idempotent update processing keyed by Telegram update_id."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy import CursorResult, delete
from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger
from app.db.database import Database
from app.db.models import ProcessedUpdate

logger = get_logger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class UpdateDedupeMiddleware(BaseMiddleware):
    def __init__(self, database: Database) -> None:
        self._database = database

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        update_id = event.update_id
        if not await self._mark(update_id):
            logger.debug("update_duplicate", update_id=update_id)
            return None

        data["update_id"] = update_id
        try:
            return await handler(event, data)
        except Exception:
            await self._unmark(update_id)
            raise

    async def _mark(self, update_id: int) -> bool:
        try:
            async with self._database.session() as session:
                session.add(ProcessedUpdate(update_id=update_id))
        except IntegrityError:
            return False
        return True

    async def _unmark(self, update_id: int) -> None:
        async with self._database.session() as session:
            record = await session.get(ProcessedUpdate, update_id)
            if record is not None:
                await session.delete(record)


async def prune_processed_updates(database: Database, *, older_than_days: int = 7) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    async with database.session() as session:
        result = await session.execute(
            delete(ProcessedUpdate).where(ProcessedUpdate.processed_at < cutoff)
        )
    return int(cast("CursorResult[Any]", result).rowcount or 0)
