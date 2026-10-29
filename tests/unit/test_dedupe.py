from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiogram.types import TelegramObject, Update

from app.bot.middlewares.dedupe import UpdateDedupeMiddleware, prune_processed_updates
from app.db.base import Base
from app.db.database import Database
from app.db.models import ProcessedUpdate


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def test_duplicate_update_is_skipped(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    middleware = UpdateDedupeMiddleware(database)
    calls = 0

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        return "handled"

    update = Update(update_id=1001)

    assert await middleware(handler, update, {}) == "handled"
    assert await middleware(handler, update, {}) is None
    assert calls == 1

    await database.dispose()


async def test_failed_handler_releases_update_for_retry(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    middleware = UpdateDedupeMiddleware(database)

    async def failing_handler(event: TelegramObject, data: dict[str, Any]) -> str:
        raise RuntimeError("boom")

    update = Update(update_id=2002)

    with pytest.raises(RuntimeError, match="boom"):
        await middleware(failing_handler, update, {})

    async def ok_handler(event: TelegramObject, data: dict[str, Any]) -> str:
        return "retried"

    assert await middleware(ok_handler, update, {}) == "retried"

    await database.dispose()


async def test_prune_removes_old_updates(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        session.add(
            ProcessedUpdate(
                update_id=1,
                processed_at=datetime.now(UTC) - timedelta(days=30),
            )
        )
        session.add(ProcessedUpdate(update_id=2, processed_at=datetime.now(UTC)))

    removed = await prune_processed_updates(database, older_than_days=7)

    assert removed == 1
    async with database.session() as session:
        assert await session.get(ProcessedUpdate, 1) is None
        assert await session.get(ProcessedUpdate, 2) is not None

    await database.dispose()
