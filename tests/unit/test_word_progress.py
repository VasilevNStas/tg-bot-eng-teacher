from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.db.base import Base
from app.db.database import Database
from app.db.enums import (
    CEFRLevel,
    Checker,
    ContentKind,
    ExerciseSessionKind,
    Verdict,
    WordDirection,
)
from app.db.models import ContentItem, ExerciseAttempt, ExerciseSession, Topic, User
from app.db.repositories.word_progress import WordProgressRepository


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def seed(database: Database) -> tuple[int, int, int]:
    async with database.session() as session:
        user = User(tg_id=100500, level=CEFRLevel.A1, profile={})
        topic = Topic(slug="core-words", title="Core words", level=CEFRLevel.A1, tags=[])
        session.add_all([user, topic])
        await session.flush()

        item = ContentItem(
            slug="word-one",
            kind=ContentKind.VOCABULARY,
            level=CEFRLevel.A1,
            payload={
                "kind": "vocabulary",
                "term": "one",
                "translations_ru": ["один"],
                "frequency": 100,
                "frequency_rank": 1,
            },
            license="unknown",
        )
        session.add(item)
        await session.flush()

        exercise = ExerciseSession(user_id=user.id, topic_id=topic.id)
        session.add(exercise)
        await session.flush()
        session.add(
            ExerciseAttempt(
                user_id=user.id,
                content_item_id=item.id,
                session_id=exercise.id,
                answer="one",
                verdict=Verdict.CORRECT,
                checker=Checker.CODE,
                direction=WordDirection.EN_RU,
            )
        )

        return user.id, item.id, exercise.id


async def test_session_defaults_and_direction_roundtrip(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user_id, item_id, session_id = await seed(database)

    async with database.session() as session:
        exercise = await session.get(ExerciseSession, session_id)
        assert exercise is not None
        assert exercise.kind is ExerciseSessionKind.DRILL
        assert exercise.meta == {}

        attempt = await session.scalar(select(ExerciseAttempt))
        assert attempt is not None
        assert attempt.direction is WordDirection.EN_RU

        progress = await WordProgressRepository(session).get_or_create(user_id, item_id)
        assert progress.shown_count == 0
        assert progress.attempt_count == 0
        assert progress.streak == 0

    await database.dispose()


async def test_word_progress_aggregates_shown_attempts_and_streak(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user_id, item_id, _ = await seed(database)
    at = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

    async with database.session() as session:
        repository = WordProgressRepository(session)
        progress = await repository.get_or_create(user_id, item_id)
        await repository.mark_shown(progress, at)
        await repository.mark_shown(progress, at)

        await repository.record_attempt(progress, Verdict.CORRECT, at)
        await repository.record_attempt(progress, Verdict.PARTIAL, at)
        await repository.record_attempt(progress, Verdict.INCORRECT, at)

        assert progress.shown_count == 2
        assert progress.attempt_count == 3
        assert progress.correct_count == 1
        assert progress.partial_count == 1
        assert progress.wrong_count == 1
        assert progress.streak == 0
        assert progress.best_streak == 1
        assert progress.last_shown_at == at
        assert progress.last_attempted_at == at

    await database.dispose()
