from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import func, select

from app.curriculum.errors import ContentConflictError, ContentReferenceError
from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.curriculum.models import ContentFile
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel, ContentKind
from app.db.models import ContentItem, ContentItemTag, Topic, TopicItem
from app.db.repositories.content import ContentRepository

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "sample.yaml"


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


def fixture_doc() -> dict[str, Any]:
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


async def count(database: Database, model: type[Any]) -> int:
    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


async def test_import_creates_topic_and_items(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    content = load_content_file(FIXTURE)

    result = await ContentImporter(database).import_file(content)

    assert (result.created, result.updated, result.skipped) == (5, 0, 0)
    assert await count(database, Topic) == 1
    assert await count(database, ContentItem) == 5
    assert await count(database, TopicItem) == 5
    assert await count(database, ContentItemTag) == 5

    async with database.session() as session:
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        assert item is not None
        assert item.kind is ContentKind.IRREGULAR_VERB
        assert item.version == 1
        assert item.payload["base"] == "go"
        assert item.source == "Test fixture"
        assert item.license == "unknown"

        memberships = list(await session.scalars(select(TopicItem).order_by(TopicItem.position)))
        assert [membership.position for membership in memberships] == list(range(5))

        tags = set(
            await session.scalars(
                select(ContentItemTag.tag).where(ContentItemTag.content_item_id == item.id)
            )
        )
        assert tags == {"grammar:irregular-verbs"}

    await database.dispose()


async def test_item_can_be_linked_to_second_topic(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await ContentImporter(database).import_file(load_content_file(FIXTURE))

    async with database.session() as session:
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        assert item is not None
        second_topic = Topic(
            slug="second-topic",
            title="Second topic",
            level=CEFRLevel.A1,
            tags=[],
        )
        session.add(second_topic)
        await session.flush()
        session.add(
            TopicItem(
                topic_id=second_topic.id,
                content_item_id=item.id,
                position=0,
            )
        )

    async with database.session() as session:
        repository = ContentRepository(session)
        first = await repository.list_active(
            topic_slug="test-topic-a1",
            kind=ContentKind.IRREGULAR_VERB,
        )
        second = await repository.list_active(
            topic_slug="second-topic",
            kind=ContentKind.IRREGULAR_VERB,
        )
        tagged = await repository.list_active(tag="grammar:irregular-verbs")
        assert [row.slug for row in first] == ["test-irregular-go"]
        assert [row.slug for row in second] == ["test-irregular-go"]
        assert [row.slug for row in tagged] == ["test-irregular-go"]

    await database.dispose()


async def test_imports_variant_lineage_and_removal(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    importer = ContentImporter(database)
    doc = fixture_doc()
    variant = copy.deepcopy(doc["items"][0])
    variant["slug"] = "test-irregular-go-variant"
    variant["variant_of"] = "test-irregular-go"
    doc["items"].append(variant)

    result = await importer.import_file(ContentFile.model_validate(doc))

    assert result.created == 6
    async with database.session() as session:
        base = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        variant_row = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go-variant")
        )
        assert base is not None and variant_row is not None
        assert variant_row.variant_of_id == base.id
        assert variant_row.version == 1

    doc["items"][-1]["variant_of"] = None
    result = await importer.import_file(ContentFile.model_validate(doc))

    assert (result.created, result.updated, result.skipped) == (0, 1, 5)
    async with database.session() as session:
        variant_row = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go-variant")
        )
        assert variant_row is not None
        assert variant_row.variant_of_id is None
        assert variant_row.version == 2

    await database.dispose()


async def test_variant_base_missing_rolls_back(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    doc = fixture_doc()
    doc["items"][0]["variant_of"] = "missing-base"
    content = ContentFile.model_validate(doc)

    with pytest.raises(ContentReferenceError):
        await ContentImporter(database).import_file(content)

    assert await count(database, Topic) == 0
    assert await count(database, ContentItem) == 0

    await database.dispose()


async def test_variant_cannot_reference_itself(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    doc = fixture_doc()
    doc["items"][0]["variant_of"] = doc["items"][0]["slug"]
    content = ContentFile.model_validate(doc)

    with pytest.raises(ContentConflictError, match="itself"):
        await ContentImporter(database).import_file(content)

    await database.dispose()


async def test_variant_cycle_is_rejected(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    doc = fixture_doc()
    doc["items"][0]["variant_of"] = doc["items"][1]["slug"]
    doc["items"][1]["variant_of"] = doc["items"][0]["slug"]
    content = ContentFile.model_validate(doc)

    with pytest.raises(ContentConflictError, match="cycle"):
        await ContentImporter(database).import_file(content)

    assert await count(database, ContentItem) == 0

    await database.dispose()


async def test_variant_base_can_follow_variant_in_batch(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    base_doc = fixture_doc()
    base_doc["items"] = [base_doc["items"][0]]

    variant_doc = fixture_doc()
    variant_doc["topic"]["slug"] = "variant-topic"
    variant = copy.deepcopy(variant_doc["items"][0])
    variant["slug"] = "cross-file-variant"
    variant["variant_of"] = "test-irregular-go"
    variant_doc["items"] = [variant]

    results = await ContentImporter(database).import_files(
        [
            ContentFile.model_validate(variant_doc),
            ContentFile.model_validate(base_doc),
        ]
    )

    assert [(result.created, result.updated, result.skipped) for result in results] == [
        (1, 0, 0),
        (1, 0, 0),
    ]
    async with database.session() as session:
        base = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        variant_row = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "cross-file-variant")
        )
        assert base is not None and variant_row is not None
        assert variant_row.variant_of_id == base.id

    await database.dispose()


async def test_second_import_is_idempotent(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    content = load_content_file(FIXTURE)
    importer = ContentImporter(database)

    await importer.import_file(content)
    result = await importer.import_file(content)

    assert (result.created, result.updated, result.skipped) == (0, 0, 5)
    async with database.session() as session:
        versions = set(await session.scalars(select(ContentItem.version)))
    assert versions == {1}

    await database.dispose()


async def test_changed_item_bumps_version(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    importer = ContentImporter(database)
    await importer.import_file(load_content_file(FIXTURE))

    doc = fixture_doc()
    doc["items"][0]["payload"]["translation_ru"] = "ходить"
    modified = ContentFile.model_validate(doc)

    result = await importer.import_file(modified)

    assert (result.created, result.updated, result.skipped) == (0, 1, 4)
    async with database.session() as session:
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        assert item is not None
        assert item.version == 2
        assert item.payload["translation_ru"] == "ходить"

    await database.dispose()


async def test_tag_change_bumps_version_and_replaces_tags(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    importer = ContentImporter(database)
    await importer.import_file(load_content_file(FIXTURE))

    doc = fixture_doc()
    doc["items"][0]["tags"] = ["grammar:irregular-verbs", "purpose:warmup"]
    modified = ContentFile.model_validate(doc)

    result = await importer.import_file(modified)

    assert (result.created, result.updated, result.skipped) == (0, 1, 4)
    async with database.session() as session:
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        assert item is not None
        assert item.version == 2
        tags = set(
            await session.scalars(
                select(ContentItemTag.tag).where(ContentItemTag.content_item_id == item.id)
            )
        )
        assert tags == {"grammar:irregular-verbs", "purpose:warmup"}

    doc["items"][0]["tags"] = ["grammar:irregular-verbs"]
    result = await importer.import_file(ContentFile.model_validate(doc))
    assert (result.created, result.updated, result.skipped) == (0, 1, 4)
    async with database.session() as session:
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        assert item is not None
        assert item.version == 3
        tags = set(
            await session.scalars(
                select(ContentItemTag.tag).where(ContentItemTag.content_item_id == item.id)
            )
        )
        assert tags == {"grammar:irregular-verbs"}

    await database.dispose()


async def test_item_deactivation_is_imported(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    importer = ContentImporter(database)
    await importer.import_file(load_content_file(FIXTURE))

    doc = fixture_doc()
    doc["items"][1]["active"] = False
    modified = ContentFile.model_validate(doc)

    result = await importer.import_file(modified)

    assert result.updated == 1
    async with database.session() as session:
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-gap-present-simple")
        )
        assert item is not None
        assert item.is_active is False

    await database.dispose()


async def test_topic_only_file_creates_topic(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    doc = fixture_doc()
    doc["topic"]["slug"] = "empty-topic"
    doc["items"] = []

    result = await ContentImporter(database).import_file(ContentFile.model_validate(doc))

    assert (result.created, result.updated, result.skipped) == (0, 0, 0)
    assert await count(database, Topic) == 1
    assert await count(database, ContentItem) == 0
    assert await count(database, TopicItem) == 0

    await database.dispose()


async def test_move_item_to_another_topic_syncs_membership(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    importer = ContentImporter(database)
    await importer.import_file(load_content_file(FIXTURE))

    doc = copy.deepcopy(fixture_doc())
    doc["topic"]["slug"] = "other-topic"
    moved = ContentFile.model_validate(doc)

    result = await importer.import_file(moved)

    assert (result.created, result.updated, result.skipped) == (0, 5, 0)
    assert await count(database, Topic) == 2
    assert await count(database, ContentItem) == 5
    assert await count(database, TopicItem) == 5

    async with database.session() as session:
        old_topic = await session.scalar(select(Topic).where(Topic.slug == "test-topic-a1"))
        new_topic = await session.scalar(select(Topic).where(Topic.slug == "other-topic"))
        assert old_topic is not None and new_topic is not None
        old_links = await session.scalar(
            select(func.count()).select_from(TopicItem).where(TopicItem.topic_id == old_topic.id)
        )
        new_links = await session.scalar(
            select(func.count()).select_from(TopicItem).where(TopicItem.topic_id == new_topic.id)
        )
        assert old_links == 0
        assert new_links == 5

    await database.dispose()


async def test_import_files_resolves_topic_from_batch(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    importer = ContentImporter(database)

    second_doc = fixture_doc()
    second_doc["topic"]["slug"] = "second-topic"
    second_doc["items"] = []
    second_topic = ContentFile.model_validate(second_doc)

    doc = fixture_doc()
    doc["items"][0]["topics"] = ["test-topic-a1", "second-topic"]
    content = ContentFile.model_validate(doc)

    results = await importer.import_files([second_topic, content])

    assert [(item.created, item.updated, item.skipped) for item in results] == [
        (0, 0, 0),
        (5, 0, 0),
    ]
    assert await count(database, Topic) == 2
    assert await count(database, TopicItem) == 6

    async with database.session() as session:
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        assert item is not None
        memberships = set(
            await session.scalars(
                select(TopicItem.topic_id).where(TopicItem.content_item_id == item.id)
            )
        )
        home = await session.scalar(select(Topic).where(Topic.slug == "test-topic-a1"))
        second = await session.scalar(select(Topic).where(Topic.slug == "second-topic"))
        assert home is not None and second is not None
        assert memberships == {home.id, second.id}

    await database.dispose()


async def test_unknown_topic_reference_rolls_back_import(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    doc = fixture_doc()
    doc["items"][0]["topics"] = ["missing-topic"]
    content = ContentFile.model_validate(doc)

    with pytest.raises(ContentReferenceError):
        await ContentImporter(database).import_file(content)

    assert await count(database, Topic) == 0
    assert await count(database, ContentItem) == 0
    assert await count(database, TopicItem) == 0

    await database.dispose()


async def test_import_files_rejects_duplicate_topic(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    doc = fixture_doc()
    doc["items"] = []
    first = ContentFile.model_validate(doc)
    second = ContentFile.model_validate(copy.deepcopy(doc))

    with pytest.raises(ContentConflictError):
        await ContentImporter(database).import_files([first, second])

    await database.dispose()
