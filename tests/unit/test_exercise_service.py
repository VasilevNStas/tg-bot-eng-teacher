from __future__ import annotations

import random
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel, ExerciseState, Verdict
from app.db.models import (
    ContentItem,
    ExerciseAttempt,
    ExerciseSession,
    User,
    UserProgress,
)
from app.db.repositories.users import UserRepository
from app.services.exercises.errors import DrillSessionError, DrillUnavailable
from app.services.exercises.service import ExerciseService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BANK = PROJECT_ROOT / "content" / "topics" / "irregular_verbs.yaml"


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def seed_bank(database: Database) -> None:
    await ContentImporter(database).import_file(load_content_file(BANK))


async def make_user(
    database: Database,
    *,
    tg_id: int = 100,
    level: CEFRLevel = CEFRLevel.A2,
) -> User:
    async with database.session() as session:
        repository = UserRepository(session)
        user = await repository.get_or_create(tg_id=tg_id, username=None)
        user.onboarding_state = "done"
        user.level = level
    return user


async def count(database: Database, model: type[object]) -> int:
    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


async def test_start_session_selects_items_and_alternates_forms(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_bank(database)
    user = await make_user(database)
    service = ExerciseService(database, session_size=3, rng=random.Random(7))

    session = await service.start_session(user)

    assert len(session.questions) == 3
    assert [question.form for question in session.questions] == [
        "past",
        "participle",
        "past",
    ]

    async with database.session() as db_session:
        item_ids = [question.content_item_id for question in session.questions]
        items = list(
            await db_session.scalars(select(ContentItem).where(ContentItem.id.in_(item_ids)))
        )
        assert len(items) == 3
        assert all(item.level is CEFRLevel.A2 for item in items)
        exercise_session = await db_session.get(ExerciseSession, session.session_id)
        assert exercise_session is not None
        assert exercise_session.state is ExerciseState.ACTIVE

    await database.dispose()


async def test_start_session_falls_back_to_other_levels(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_bank(database)
    user = await make_user(database, level=CEFRLevel.C2)
    service = ExerciseService(database, session_size=3, rng=random.Random(7))

    session = await service.start_session(user)

    assert len(session.questions) == 3

    await database.dispose()


async def test_start_session_abandons_previous_active_session(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_bank(database)
    user = await make_user(database)
    service = ExerciseService(database, session_size=2, rng=random.Random(7))

    first = await service.start_session(user)
    second = await service.start_session(user)

    async with database.session() as db_session:
        first_row = await db_session.get(ExerciseSession, first.session_id)
        second_row = await db_session.get(ExerciseSession, second.session_id)
        assert first_row is not None and first_row.state is ExerciseState.ABANDONED
        assert second_row is not None and second_row.state is ExerciseState.ACTIVE

    await database.dispose()


async def test_submit_correct_and_incorrect_updates_progress(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_bank(database)
    user = await make_user(database)
    service = ExerciseService(database, session_size=2, rng=random.Random(7))
    session = await service.start_session(user)

    first = session.questions[0]
    correct = await service.submit_answer(
        user,
        session_id=session.session_id,
        content_item_id=first.content_item_id,
        form=first.form,
        answer=first.forms[0],
        latency_ms=123,
    )
    second = session.questions[1]
    incorrect = await service.submit_answer(
        user,
        session_id=session.session_id,
        content_item_id=second.content_item_id,
        form=second.form,
        answer="zzzz",
    )

    assert correct.result.verdict is Verdict.CORRECT
    assert correct.mastery == 0.2
    assert correct.streak == 1
    assert incorrect.result.verdict is Verdict.INCORRECT
    assert incorrect.mastery == 0.16
    assert incorrect.streak == 0
    assert await count(database, ExerciseAttempt) == 2

    async with database.session() as db_session:
        attempt = await db_session.scalar(
            select(ExerciseAttempt).order_by(ExerciseAttempt.id).limit(1)
        )
        assert attempt is not None
        assert attempt.answer == first.forms[0]
        assert attempt.latency_ms == 123

        progress = await db_session.scalar(select(UserProgress))
        assert progress is not None
        assert progress.mastery == pytest.approx(0.16)

    stats = await service.finish_session(
        user,
        session.session_id,
        state=ExerciseState.FINISHED,
    )
    assert (stats.total, stats.correct, stats.partial, stats.incorrect) == (2, 1, 0, 1)

    async with database.session() as db_session:
        exercise_session = await db_session.get(ExerciseSession, session.session_id)
        assert exercise_session is not None
        assert exercise_session.state is ExerciseState.FINISHED
        assert exercise_session.finished_at is not None

    await database.dispose()


async def test_submit_typo_is_partial(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_bank(database)
    user = await make_user(database)
    service = ExerciseService(database, session_size=1, rng=random.Random(7))
    session = await service.start_session(user)
    question = session.questions[0]
    answer = question.forms[0][1:] if len(question.forms[0]) > 1 else "x"

    result = await service.submit_answer(
        user,
        session_id=session.session_id,
        content_item_id=question.content_item_id,
        form=question.form,
        answer=answer,
    )

    assert result.result.verdict is Verdict.PARTIAL
    assert result.result.is_typo
    assert result.mastery == 0.1
    assert result.streak == 0

    await database.dispose()


async def test_cancel_active_covers_lost_fsm_state(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_bank(database)
    user = await make_user(database)
    service = ExerciseService(database, session_size=1, rng=random.Random(7))
    session = await service.start_session(user)

    stats = await service.cancel_active(user)

    assert stats is not None
    assert stats.total == 0
    async with database.session() as db_session:
        exercise_session = await db_session.get(ExerciseSession, session.session_id)
        assert exercise_session is not None
        assert exercise_session.state is ExerciseState.ABANDONED

    assert await service.cancel_active(user) is None

    await database.dispose()


async def test_foreign_user_cannot_submit(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_bank(database)
    owner = await make_user(database)
    other = await make_user(database, tg_id=200)
    service = ExerciseService(database, session_size=1, rng=random.Random(7))
    session = await service.start_session(owner)
    question = session.questions[0]

    with pytest.raises(DrillSessionError):
        await service.submit_answer(
            other,
            session_id=session.session_id,
            content_item_id=question.content_item_id,
            form=question.form,
            answer="went",
        )

    await database.dispose()


async def test_start_session_without_content_fails(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = ExerciseService(database, session_size=2)

    with pytest.raises(DrillUnavailable):
        await service.start_session(user)

    await database.dispose()
