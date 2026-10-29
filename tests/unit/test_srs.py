from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.db.base import Base
from app.db.database import Database
from app.db.models import SrsReview, User, Vocabulary
from app.services.vocabulary.srs import ReviewGrade, ReviewNotFound, SrsService, schedule

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def make_user(database: Database, tg_id: int = 1) -> User:
    async with database.session() as session:
        user = User(tg_id=tg_id)
        session.add(user)
    return user


async def add_word(database: Database, user: User, term: str = "cinema") -> Vocabulary:
    async with database.session() as session:
        word = Vocabulary(
            user_id=user.id,
            term=term,
            translation="кинотеатр",
            context="I went to the cinema.",
        )
        session.add(word)
    return word


def new_review() -> SrsReview:
    return SrsReview(
        vocabulary_id=1,
        due_at=NOW,
        interval_days=0,
        ease=2.5,
        reps=0,
        lapses=0,
    )


def test_good_intervals_progress() -> None:
    review = new_review()
    schedule(review, ReviewGrade.GOOD, NOW)
    assert (review.reps, review.interval_days, review.lapses) == (1, 1, 0)
    assert review.due_at == NOW + timedelta(days=1)
    assert review.ease == pytest.approx(2.5)

    schedule(review, ReviewGrade.GOOD, NOW)
    assert (review.reps, review.interval_days) == (2, 6)
    assert review.due_at == NOW + timedelta(days=6)

    schedule(review, ReviewGrade.GOOD, NOW)
    assert (review.reps, review.interval_days) == (3, 15)


def test_again_resets_and_schedules_soon() -> None:
    review = new_review()
    schedule(review, ReviewGrade.EASY, NOW)
    assert review.ease == pytest.approx(2.6)

    schedule(review, ReviewGrade.AGAIN, NOW)

    assert review.reps == 0
    assert review.lapses == 1
    assert review.interval_days == 0
    assert review.ease == pytest.approx(2.4)
    assert review.due_at == NOW + timedelta(minutes=10)


def test_easy_intervals_and_ease_floor() -> None:
    review = new_review()
    schedule(review, ReviewGrade.EASY, NOW)
    assert review.interval_days == 4
    assert review.ease == pytest.approx(2.6)

    schedule(review, ReviewGrade.EASY, NOW)
    assert review.interval_days == 10
    assert review.ease == pytest.approx(2.7)

    for _ in range(20):
        schedule(review, ReviewGrade.AGAIN, NOW)
    assert review.ease == pytest.approx(1.3)


async def test_due_cards_ensures_rows_and_review_hides_card(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    word = await add_word(database, user)
    service = SrsService(database)

    cards = await service.due_cards(user.id, now=NOW)
    assert [card.vocabulary_id for card in cards] == [word.id]
    assert cards[0].term == "cinema"
    assert await count(database, SrsReview) == 1

    await service.review(user.id, word.id, ReviewGrade.GOOD, now=NOW)

    assert await service.due_cards(user.id, now=NOW) == []
    future = await service.due_cards(user.id, now=NOW + timedelta(days=1))
    assert [card.vocabulary_id for card in future] == [word.id]

    await database.dispose()


async def test_review_rejects_foreign_card(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    owner = await make_user(database, tg_id=1)
    other = await make_user(database, tg_id=2)
    word = await add_word(database, owner)
    await SrsService(database).due_cards(owner.id, now=NOW)

    with pytest.raises(ReviewNotFound):
        await SrsService(database).review(other.id, word.id, ReviewGrade.GOOD, now=NOW)

    await database.dispose()


async def count(database: Database, model: type[object]) -> int:
    from sqlalchemy import func, select

    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)
