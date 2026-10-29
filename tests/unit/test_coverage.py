from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.curriculum.coverage import CoverageService, format_coverage
from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel, ContentKind
from app.db.models import ContentItem, ContentItemTag, Topic

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "sample.yaml"


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ContentImporter(database).import_file(load_content_file(FIXTURE))
    return database


async def test_coverage_reports_matrix_tags_uncollected_and_duplicates(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path)

    async with database.session() as session:
        session.add(
            Topic(
                slug="empty-topic",
                title="Empty topic",
                level=CEFRLevel.A1,
                tags=[],
            )
        )
        source = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-gap-present-simple")
        )
        assert source is not None
        duplicate = ContentItem(
            slug="test-gap-present-simple-copy",
            version=1,
            is_active=True,
            kind=ContentKind.GAP_FILL,
            level=CEFRLevel.A1,
            payload=dict(source.payload),
            source=source.source,
            license="cc0",
        )
        session.add(duplicate)
        await session.flush()
        session.add(ContentItemTag(content_item_id=duplicate.id, tag="theme:cinema"))
        await session.flush()

    report = await CoverageService(database).build()

    assert report.active_items == 6
    assert report.topics == 2
    assert report.memberships == 5
    assert report.uncollected == ("test-gap-present-simple-copy",)
    assert report.empty_topics == ("empty-topic",)
    assert report.license_unknown == (
        "test-gap-present-simple",
        "test-irregular-go",
        "test-mc-since",
        "test-translation-cinema",
        "test-word-order-cinema",
    )
    assert any(
        cell.topic_slug == "test-topic-a1"
        and cell.level == CEFRLevel.A1
        and cell.kind == ContentKind.IRREGULAR_VERB
        and cell.count == 1
        for cell in report.cells
    )
    assert any(tag.tag == "theme:cinema" and tag.count == 2 for tag in report.tags)
    assert len(report.duplicates) == 1
    duplicate_group = report.duplicates[0]
    assert duplicate_group.signature.startswith("gap_fill |")
    assert duplicate_group.slugs == (
        "test-gap-present-simple",
        "test-gap-present-simple-copy",
    )

    text = format_coverage(report)
    assert "Content coverage" in text
    assert "test-topic-a1 | A1 | irregular_verb | 1" in text
    assert "Topics without items:\nempty-topic" in text
    assert "theme:cinema | 2" in text
    assert "test-gap-present-simple, test-gap-present-simple-copy" in text
    assert "Items with unknown license:" in text

    await database.dispose()


async def test_coverage_is_empty_without_content(tmp_path: Path) -> None:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    report = await CoverageService(database).build()

    assert report.active_items == 0
    assert report.topics == 0
    assert report.cells == ()
    assert report.duplicates == ()
    assert "(no items)" in format_coverage(report)

    await database.dispose()
