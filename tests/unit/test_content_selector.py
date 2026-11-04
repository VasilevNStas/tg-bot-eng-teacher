from __future__ import annotations

import random
from pathlib import Path

import pytest

from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.curriculum.selector import ContentSelector, SelectionCriteria
from app.db.base import Base
from app.db.database import Database
from app.db.enums import ContentKind
from app.db.repositories.content import ContentRepository

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "sample.yaml"


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ContentImporter(database).import_file(load_content_file(FIXTURE))
    return database


async def test_select_filters_by_kind_and_topic(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        selector = ContentSelector(
            ContentRepository(session),
            rng=random.Random(7),
        )
        items = await selector.select(
            SelectionCriteria(
                kind=ContentKind.IRREGULAR_VERB,
                topic_slug="test-topic-a1",
            )
        )

        assert [item.slug for item in items] == ["test-irregular-go"]

    await database.dispose()


async def test_select_excludes_ids_and_applies_limit(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        repository = ContentRepository(session)
        selector = ContentSelector(repository, rng=random.Random(7))
        all_items = await selector.select(SelectionCriteria())
        assert len(all_items) == 5

        excluded = frozenset({all_items[0].id})
        selected = await selector.select(SelectionCriteria(exclude_ids=excluded, limit=3))

        assert len(selected) == 3
        assert all(item.id not in excluded for item in selected)
        assert await selector.select(SelectionCriteria(limit=0)) == []

    await database.dispose()


async def test_select_rejects_negative_limit(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        selector = ContentSelector(ContentRepository(session))
        with pytest.raises(ValueError, match="negative"):
            await selector.select(SelectionCriteria(limit=-1))

    await database.dispose()


async def test_select_returns_empty_without_matches(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        selector = ContentSelector(ContentRepository(session))
        items = await selector.select(SelectionCriteria(topic_slug="missing-topic"))

        assert items == []

    await database.dispose()
