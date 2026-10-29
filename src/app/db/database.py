"""Async database engine and session management."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.logging import get_logger

logger = get_logger(__name__)


def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


class Database:
    """Owns the engine and session factory for the application lifetime."""

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database is not initialized")
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            raise RuntimeError("Database is not initialized")
        return self._sessionmaker

    def init(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_from_url(f"sqlite+aiosqlite:///{db_path}")

    def init_from_url(self, url: str | URL, **engine_kwargs: Any) -> None:
        if self._engine is not None:
            raise RuntimeError("Database is already initialized")

        engine = create_async_engine(url, **engine_kwargs)
        if engine.url.get_backend_name() == "sqlite":
            event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)

        self._engine = engine
        self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        logger.info("db_initialized", backend=engine.url.get_backend_name())

    def configure(
        self,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Inject a prepared engine and session factory, primarily for tests."""
        self._engine = engine
        self._sessionmaker = sessionmaker
        logger.debug("db_configured")

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        session = self.sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            logger.debug("db_session_rollback", exc_info=True)
            raise
        finally:
            await session.close()

    @contextlib.asynccontextmanager
    async def connect(self) -> AsyncIterator[AsyncConnection]:
        async with self.engine.begin() as connection:
            yield connection

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None
            logger.info("db_disposed")
