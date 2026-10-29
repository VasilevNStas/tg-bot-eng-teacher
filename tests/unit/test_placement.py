from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.curriculum.placement import QUESTIONS, load_placement_questions, score_to_level
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel
from app.db.models import ContentItem

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "placement.yaml"


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


def test_score_maps_to_expected_levels() -> None:
    assert score_to_level(0) is CEFRLevel.A1
    assert score_to_level(1) is CEFRLevel.A1
    assert score_to_level(2) is CEFRLevel.A2
    assert score_to_level(3) is CEFRLevel.B1
    assert score_to_level(4) is CEFRLevel.B2
    assert score_to_level(5) is CEFRLevel.C1
    assert score_to_level(6) is CEFRLevel.C2


def test_score_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        score_to_level(-1)
    with pytest.raises(ValueError):
        score_to_level(7)


def test_questions_are_well_formed() -> None:
    assert [question.level for question in QUESTIONS] == list(CEFRLevel)

    for question in QUESTIONS:
        assert question.prompt
        assert len(question.options) == 3
        assert len(set(question.options)) == 3
        assert 0 <= question.correct_index < len(question.options)


async def test_loader_falls_back_without_bank(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    assert await load_placement_questions(database) == QUESTIONS

    await database.dispose()


async def test_loader_uses_complete_bank_sorted_by_level(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await ContentImporter(database).import_file(load_content_file(FIXTURE))

    questions = await load_placement_questions(database)

    assert [question.level for question in questions] == list(CEFRLevel)
    assert all(question.prompt.startswith("BANK:") for question in questions)
    assert questions[0].options == ("am", "is", "are")
    assert questions[-1].correct_index == 1

    await database.dispose()


async def test_loader_falls_back_on_incomplete_bank(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await ContentImporter(database).import_file(load_content_file(FIXTURE))
    async with database.session() as session:
        item = await session.scalar(
            select(ContentItem).where(ContentItem.slug == "test-placement-b2")
        )
        assert item is not None
        item.is_active = False

    assert await load_placement_questions(database) == QUESTIONS

    await database.dispose()
