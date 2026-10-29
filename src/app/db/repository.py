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
    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self._session = session
        self._model = model

    async def get(self, pk: int) -> ModelT | None:
        return await self._session.get(self._model, pk)

    async def list_all(self) -> list[ModelT]:
        return await self._scalars(select(self._model))

    async def add(self, **values: Any) -> ModelT:
        instance = self._model(**values)
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def delete(self, instance: ModelT) -> None:
        await self._session.delete(instance)

    async def _scalars(self, stmt: Select[Any]) -> list[ModelT]:
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
