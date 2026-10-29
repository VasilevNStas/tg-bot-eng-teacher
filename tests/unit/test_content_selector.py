from __future__ import annotations

import random
from pathlib import Path

import pytest
from sqlalchemy import select

from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.curriculum.selector import ContentSelector, SelectionCriteria
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel, ContentKind
from app.db.models import ContentItem, ContentItemTag, TopicItem
from app.db.repositories.content import ContentRepository

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "sample.yaml"


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ContentImporter(database).import_file(load_content_file(FIXTURE))
    return database


async def test_select_filters_by_kind_level_topic_and_tag(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        selector = ContentSelector(
            ContentRepository(session),
            rng=random.Random(7),
        )
        items = await selector.select(
            SelectionCriteria(
                kind=ContentKind.IRREGULAR_VERB,
                level=CEFRLevel.A1,
                topic_slug="test-topic-a1",
                tags=("grammar:irregular-verbs",),
            )
        )

        assert [item.slug for item in items] == ["test-irregular-go"]

    await database.dispose()


async def test_select_requires_all_tags(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        assert item is not None
        session.add(
            ContentItemTag(
                content_item_id=item.id,
                tag="purpose:warmup",
            )
        )
        await session.flush()

        selector = ContentSelector(ContentRepository(session))
        matching = await selector.select(
            SelectionCriteria(
                tags=("grammar:irregular-verbs", "purpose:warmup"),
            )
        )
        not_matching = await selector.select(
            SelectionCriteria(
                tags=("grammar:irregular-verbs", "theme:cinema"),
            )
        )

        assert [row.slug for row in matching] == ["test-irregular-go"]
        assert not_matching == []

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


async def test_select_deduplicates_variant_family(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        base = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        assert base is not None
        link = await session.scalar(select(TopicItem).where(TopicItem.content_item_id == base.id))
        assert link is not None
        variant = ContentItem(
            slug="test-irregular-go-variant",
            variant_of_id=base.id,
            version=1,
            is_active=True,
            kind=base.kind,
            level=base.level,
            payload=dict(base.payload),
            source=base.source,
            license=base.license,
        )
        session.add(variant)
        await session.flush()
        session.add(
            TopicItem(
                topic_id=link.topic_id,
                content_item_id=variant.id,
                position=link.position + 1,
            )
        )

    async with database.session() as session:
        selector = ContentSelector(ContentRepository(session))
        items = await selector.select(
            SelectionCriteria(
                topic_slug="test-topic-a1",
                kind=ContentKind.IRREGULAR_VERB,
            )
        )
        assert [item.slug for item in items] == ["test-irregular-go"]

        without_base = await selector.select(
            SelectionCriteria(
                topic_slug="test-topic-a1",
                kind=ContentKind.IRREGULAR_VERB,
                exclude_ids=frozenset({base.id}),
            )
        )
        assert [item.slug for item in without_base] == ["test-irregular-go-variant"]

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
        items = await selector.select(SelectionCriteria(tags=("exam:pet",)))

        assert items == []

    await database.dispose()
