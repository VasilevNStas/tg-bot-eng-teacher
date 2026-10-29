from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.database import Database


def test_init_creates_parent_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "bot.db"
    database = Database()

    database.init(db_path)

    assert isinstance(database.engine, AsyncEngine)
    assert db_path.parent.is_dir()


def test_uninitialized_database_raises() -> None:
    database = Database()

    with pytest.raises(RuntimeError, match="not initialized"):
        _ = database.engine


async def test_session_commits(tmp_path: Path) -> None:
    database = Database()
    database.init(tmp_path / "bot.db")

    async with database.session() as session:
        await session.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)"))

    async with database.session() as session:
        await session.execute(text("INSERT INTO items (value) VALUES ('kept')"))

    async with database.session() as session:
        result = await session.execute(text("SELECT value FROM items"))
        assert result.scalar_one() == "kept"

    await database.dispose()


async def test_session_rolls_back_on_error(tmp_path: Path) -> None:
    database = Database()
    database.init(tmp_path / "bot.db")

    async with database.session() as session:
        await session.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)"))

    with pytest.raises(RuntimeError, match="boom"):
        async with database.session() as session:
            await session.execute(text("INSERT INTO items (value) VALUES ('rolled-back')"))
            raise RuntimeError("boom")

    async with database.session() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM items"))
        assert result.scalar_one() == 0

    await database.dispose()
