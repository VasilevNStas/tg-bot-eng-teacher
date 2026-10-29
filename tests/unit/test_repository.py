from __future__ import annotations

from pathlib import Path

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.database import Database
from app.db.repository import BaseRepository


class Sample(TimestampMixin, Base):
    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))


async def test_repository_crud(tmp_path: Path) -> None:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with database.session() as session:
        repository = BaseRepository(session, Sample)
        created = await repository.add(name="alpha")
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None

    async with database.session() as session:
        repository = BaseRepository(session, Sample)
        fetched = await repository.get(created.id)
        assert fetched is not None
        assert fetched.name == "alpha"

        assert [item.name for item in await repository.list_all()] == ["alpha"]

        await repository.delete(fetched)

    async with database.session() as session:
        repository = BaseRepository(session, Sample)
        assert await repository.list_all() == []

    await database.dispose()
