"""Generic repository with no transaction management.

The transaction boundary is ``Database.session()``: repositories only use the
session they are given. Domain repositories subclass ``BaseRepository`` and add
model-specific queries.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base


class BaseRepository[ModelT: Base]:
    """Small CRUD surface shared by domain repositories."""

    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self._session = session
        self._model = model

    async def get(self, pk: int) -> ModelT | None:
        """Fetch by primary key, returning None when absent."""
        return await self._session.get(self._model, pk)

    async def list_all(self) -> list[ModelT]:
        """Return every row (callers should paginate in domain repositories)."""
        return await self._scalars(select(self._model))

    async def add(self, **values: Any) -> ModelT:
        """Insert a row and flush so the caller gets the generated id."""
        instance = self._model(**values)
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def delete(self, instance: ModelT) -> None:
        """Mark a row for deletion; the session commits on exit."""
        await self._session.delete(instance)

    async def _scalars(self, stmt: Select[Any]) -> list[ModelT]:
        """Execute a select and materialize its scalar column."""
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
