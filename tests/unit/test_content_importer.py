from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import func, select

from app.curriculum.errors import ContentConflictError
from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.curriculum.models import ContentFile
from app.db.base import Base
from app.db.database import Database
from app.db.enums import ContentKind
from app.db.models import ContentItem, Topic
from app.db.repositories.content import ContentRepository

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "sample.yaml"
VOCAB_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "vocabulary.yaml"


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

    async with database.session() as session:
        topic = await session.scalar(select(Topic).where(Topic.slug == "test-topic-a1"))
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        assert topic is not None
        assert item is not None
        assert item.topic_id == topic.id
        assert item.kind is ContentKind.IRREGULAR_VERB
        assert item.payload["base"] == "go"
        assert item.source == "Test fixture"
        assert item.license == "unknown"

    await database.dispose()


async def test_import_vocabulary_payload(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    content = load_content_file(VOCAB_FIXTURE)

    result = await ContentImporter(database).import_file(content)

    assert result.created == 3
    async with database.session() as session:
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-vocabulary-care")
        )
        assert item is not None
        assert item.kind is ContentKind.VOCABULARY
        assert item.payload["term"] == "care"
        assert item.payload["frequency"] == 9482
        assert item.payload["frequency_rank"] == 315
        assert item.payload["translation_ru"] == "забота, беспокойство"

    await database.dispose()


async def test_second_import_is_idempotent(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    content = load_content_file(FIXTURE)
    importer = ContentImporter(database)

    await importer.import_file(content)
    result = await importer.import_file(content)

    assert (result.created, result.updated, result.skipped) == (0, 0, 5)

    await database.dispose()


async def test_changed_item_is_updated(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    importer = ContentImporter(database)
    await importer.import_file(load_content_file(FIXTURE))

    doc = fixture_doc()
    doc["items"][0]["translation_ru"] = "ходить"
    modified = ContentFile.model_validate(doc)

    result = await importer.import_file(modified)

    assert (result.created, result.updated, result.skipped) == (0, 1, 4)
    async with database.session() as session:
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-irregular-go")
        )
        assert item is not None
        assert item.payload["translation_ru"] == "ходить"

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

    await database.dispose()


async def test_move_item_to_another_topic_updates_topic_id(tmp_path: Path) -> None:
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

    async with database.session() as session:
        old_topic = await session.scalar(select(Topic).where(Topic.slug == "test-topic-a1"))
        new_topic = await session.scalar(select(Topic).where(Topic.slug == "other-topic"))
        assert old_topic is not None and new_topic is not None
        old_items = await session.scalar(
            select(func.count())
            .select_from(ContentItem)
            .where(ContentItem.topic_id == old_topic.id)
        )
        new_items = await session.scalar(
            select(func.count())
            .select_from(ContentItem)
            .where(ContentItem.topic_id == new_topic.id)
        )
        assert old_items == 0
        assert new_items == 5

    await database.dispose()


async def test_repository_filters_by_topic_and_kind(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await ContentImporter(database).import_file(load_content_file(FIXTURE))

    async with database.session() as session:
        repository = ContentRepository(session)
        verbs = await repository.list_active(
            topic_slug="test-topic-a1",
            kind=ContentKind.IRREGULAR_VERB,
        )
        gaps = await repository.list_active(
            topic_slug="test-topic-a1",
            kind=ContentKind.GAP_FILL,
        )
        assert [row.slug for row in verbs] == ["test-irregular-go"]
        assert gaps

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
