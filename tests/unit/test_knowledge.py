from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.curriculum.models import ContentFile, KnowledgePayload
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel
from app.services.knowledge.model import KnowledgeSnippet, render_knowledge_block
from app.services.knowledge.service import KnowledgeService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = PROJECT_ROOT / "content" / "knowledge" / "grammar.yaml"


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def seed_knowledge(database: Database) -> None:
    await ContentImporter(database).import_file(load_content_file(KNOWLEDGE))


def profile(*, goals: list[str] | None = None, interests: list[str] | None = None) -> dict:
    return {
        "goals": {
            "goals": goals or [],
            "interests": interests or [],
            "daily_items": None,
        }
    }


def level_content() -> ContentFile:
    return ContentFile.model_validate(
        {
            "topic": {"slug": "knowledge-level", "title": "Level"},
            "items": [
                {
                    "slug": "knowledge-level-c1",
                    "kind": "knowledge",
                    "title": "C1 only",
                    "body_md": "Advanced explanation.",
                    "tags": ["grammar"],
                    "level": "C1",
                }
            ],
        }
    )


def test_knowledge_payload_validates_and_normalizes_tags() -> None:
    payload = KnowledgePayload(
        title="Articles",
        body_md="Use a/an for one of many.",
        tags=[" Articles ", "GRAMMAR"],
    )

    assert payload.tags == ["articles", "grammar"]


def test_knowledge_payload_rejects_bad_tags() -> None:
    with pytest.raises(ValidationError):
        KnowledgePayload(title="Bad", body_md="text", tags=["has space"])

    with pytest.raises(ValidationError):
        KnowledgePayload(title="Bad", body_md="text", tags=["grammar", "grammar"])


def test_knowledge_payload_rejects_multiline_title() -> None:
    with pytest.raises(ValidationError):
        KnowledgePayload(title="two\nlines", body_md="text", tags=["grammar"])


def test_project_knowledge_file_loads() -> None:
    content = load_content_file(KNOWLEDGE)

    assert content.topic.slug == "knowledge-grammar"
    assert len(content.items) == 12
    assert all(isinstance(item.payload, KnowledgePayload) for item in content.items)


@pytest.mark.asyncio
async def test_selects_by_goal(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_knowledge(database)

    snippets = await KnowledgeService(database).select_for_profile(profile(goals=["articles"]))

    assert [snippet.slug for snippet in snippets] == ["knowledge-articles"]


@pytest.mark.asyncio
async def test_selects_by_interest(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_knowledge(database)

    snippets = await KnowledgeService(database).select_for_profile(profile(interests=["it"]))

    assert snippets
    assert snippets[0].slug in {"knowledge-business-email", "knowledge-it-vocab"}


@pytest.mark.asyncio
async def test_returns_empty_without_preferences(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_knowledge(database)

    assert await KnowledgeService(database).select_for_profile({}) == ()


@pytest.mark.asyncio
async def test_respects_item_limit(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_knowledge(database)

    snippets = await KnowledgeService(database, limit=1).select_for_profile(
        profile(goals=["grammar"])
    )

    assert len(snippets) == 1


@pytest.mark.asyncio
async def test_respects_char_limit(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_knowledge(database)

    snippets = await KnowledgeService(database, max_chars=10).select_for_profile(
        profile(goals=["grammar"])
    )

    assert snippets == ()


@pytest.mark.asyncio
async def test_filters_by_level(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_knowledge(database)
    await ContentImporter(database).import_file(level_content())

    service = KnowledgeService(database, limit=20, max_chars=10_000)

    b1 = await service.select_for_profile(profile(goals=["grammar"]), level=CEFRLevel.B1)
    c1 = await service.select_for_profile(profile(goals=["grammar"]), level=CEFRLevel.C1)

    assert "knowledge-level-c1" not in {snippet.slug for snippet in b1}
    assert "knowledge-level-c1" in {snippet.slug for snippet in c1}


def test_render_knowledge_block() -> None:
    snippet = KnowledgeSnippet(
        slug="knowledge-articles",
        title="Articles",
        body_md="Use **a** for one of many.",
        tags=("articles",),
    )

    block = render_knowledge_block([snippet])

    assert block is not None
    assert "Curated knowledge" in block
    assert "### Articles" in block
    assert "Use **a** for one of many." in block


def test_render_empty_knowledge_block() -> None:
    assert render_knowledge_block([]) is None
