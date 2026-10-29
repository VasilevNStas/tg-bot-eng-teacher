from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db.base import Base
from app.db.database import Database
from app.db.enums import (
    CEFRLevel,
    ContentKind,
    ExerciseSessionKind,
    ExerciseState,
    Verdict,
    WordDirection,
)
from app.db.models import ContentItem, ExerciseSession, Topic, User
from app.db.repositories.word_progress import WordProgressRepository
from app.services.words.errors import WordCardSessionError, WordCardUnavailable
from app.services.words.service import WordCardDirection, WordCardService

TRANSLATIONS = [
    "один",
    "два",
    "три",
    "четыре",
    "пять",
    "шесть",
    "семь",
    "восемь",
    "девять",
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
    "двадцать",
]
TERMS = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def seed_vocabulary(database: Database, *, count: int = 10) -> list[int]:
    async with database.session() as session:
        topic = Topic(slug="core-words", title="Core words", level=CEFRLevel.A1, tags=[])
        session.add(topic)
        await session.flush()

        items: list[ContentItem] = []
        for index in range(count):
            item = ContentItem(
                slug=f"word-{TERMS[index]}",
                kind=ContentKind.VOCABULARY,
                level=CEFRLevel.A1,
                payload={
                    "kind": "vocabulary",
                    "term": TERMS[index],
                    "translations_ru": [TRANSLATIONS[index]],
                    "accepted_en": [TERMS[index]],
                    "frequency": 1_000_000 - index * 1000,
                    "frequency_rank": index + 1,
                },
                license="unknown",
            )
            session.add(item)
            items.append(item)
        await session.flush()
        return [item.id for item in items]


async def make_user(
    database: Database,
    *,
    tg_id: int = 100,
    level: CEFRLevel = CEFRLevel.A1,
) -> User:
    async with database.session() as session:
        user = User(tg_id=tg_id, level=level, profile={})
        session.add(user)
        await session.flush()
        return user


async def mark_shown(database: Database, user: User, item_ids: list[int]) -> None:
    at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    async with database.session() as session:
        repository = WordProgressRepository(session)
        for item_id in item_ids:
            progress = await repository.get_or_create(user.id, item_id)
            await repository.mark_shown(progress, at)


def service(database: Database, *, card_size: int = 5, seed: int = 7) -> WordCardService:
    return WordCardService(database, card_size=card_size, rng=random.Random(seed))


async def test_start_card_creates_words_session_and_snapshot(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_vocabulary(database, count=10)
    user = await make_user(database)

    card = await service(database).start_card(user, direction=WordCardDirection.EN_RU)

    assert len(card.questions) == 5
    assert len(card.item_ids) == 5
    assert all(question.direction is WordDirection.EN_RU for question in card.questions)

    async with database.session() as session:
        exercise_session = await session.get(ExerciseSession, card.session_id)
        assert exercise_session is not None
        assert exercise_session.kind is ExerciseSessionKind.WORDS
        assert exercise_session.state is ExerciseState.ACTIVE
        assert exercise_session.topic_id is None
        assert exercise_session.meta["card_size"] == 5
        assert exercise_session.meta["direction"] == "en_ru"
        assert exercise_session.meta["source"] == "adaptive"
        assert len(exercise_session.meta["item_ids"]) == 5

        progress = await WordProgressRepository(session).list_for_user(user.id)
        assert set(progress) == set(card.item_ids)
        assert all(row.shown_count == 1 for row in progress.values())

    await database.dispose()


async def test_new_word_quota_is_capped(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    item_ids = await seed_vocabulary(database, count=10)
    user = await make_user(database)
    await mark_shown(database, user, item_ids[:7])

    card = await service(database, card_size=5).start_card(user)

    async with database.session() as session:
        progress = await WordProgressRepository(session).list_for_user(user.id)
    new_items = [item_id for item_id in card.item_ids if progress[item_id].shown_count == 0]
    assert len(new_items) <= 2

    await database.dispose()


async def test_recent_cards_are_excluded(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_vocabulary(database, count=20)
    user = await make_user(database)
    cards = service(database, card_size=5)

    first = await cards.start_card(user)
    second = await cards.start_card(user)

    assert set(first.item_ids).isdisjoint(second.item_ids)

    await database.dispose()


async def test_both_direction_doubles_questions(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_vocabulary(database, count=10)
    user = await make_user(database)

    card = await service(database, card_size=3).start_card(
        user,
        direction=WordCardDirection.BOTH,
    )

    assert len(card.item_ids) == 3
    assert len(card.questions) == 6
    assert [question.direction for question in card.questions] == [
        WordDirection.EN_RU,
        WordDirection.RU_EN,
        WordDirection.EN_RU,
        WordDirection.RU_EN,
        WordDirection.EN_RU,
        WordDirection.RU_EN,
    ]

    await database.dispose()


async def test_mix_direction_uses_single_random_direction_per_word(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_vocabulary(database, count=10)
    user = await make_user(database)

    card = await service(database, card_size=5).start_card(
        user,
        direction=WordCardDirection.MIX,
    )

    assert len(card.questions) == 5
    assert all(
        question.direction in (WordDirection.EN_RU, WordDirection.RU_EN)
        for question in card.questions
    )

    await database.dispose()


async def test_en_ru_accepts_translation_without_fuzzy(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_vocabulary(database, count=10)
    user = await make_user(database)
    cards = service(database, card_size=1)
    card = await cards.start_card(user, direction=WordCardDirection.EN_RU)
    question = card.questions[0]

    correct = await cards.submit_answer(
        user,
        session_id=card.session_id,
        content_item_id=question.content_item_id,
        direction=WordDirection.EN_RU,
        answer=question.translations_ru[0],
    )
    typo = await cards.submit_answer(
        user,
        session_id=card.session_id,
        content_item_id=question.content_item_id,
        direction=WordDirection.EN_RU,
        answer=question.translations_ru[0][:-1] + "ь",
    )

    assert correct.result.verdict is Verdict.CORRECT
    assert correct.attempt_count == 1
    assert correct.streak == 1
    assert typo.result.verdict is Verdict.INCORRECT
    assert typo.streak == 0

    await database.dispose()


async def test_ru_en_accepts_english_and_marks_typo_partial(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_vocabulary(database, count=10)
    user = await make_user(database)
    cards = service(database, card_size=1)
    card = await cards.start_card(user, direction=WordCardDirection.RU_EN)
    question = card.questions[0]

    correct = await cards.submit_answer(
        user,
        session_id=card.session_id,
        content_item_id=question.content_item_id,
        direction=WordDirection.RU_EN,
        answer=question.term,
    )
    typo = await cards.submit_answer(
        user,
        session_id=card.session_id,
        content_item_id=question.content_item_id,
        direction=WordDirection.RU_EN,
        answer=question.term[:-1] + "x",
    )

    assert correct.result.verdict is Verdict.CORRECT
    assert typo.result.verdict is Verdict.PARTIAL
    assert typo.result.is_typo

    await database.dispose()


async def test_finish_card_reports_wrong_items(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_vocabulary(database, count=10)
    user = await make_user(database)
    cards = service(database, card_size=2)
    card = await cards.start_card(user, direction=WordCardDirection.EN_RU)

    correct, wrong = card.questions
    await cards.submit_answer(
        user,
        session_id=card.session_id,
        content_item_id=correct.content_item_id,
        direction=WordDirection.EN_RU,
        answer=correct.translations_ru[0],
    )
    await cards.submit_answer(
        user,
        session_id=card.session_id,
        content_item_id=wrong.content_item_id,
        direction=WordDirection.EN_RU,
        answer="zzz",
    )

    stats = await cards.finish_card(user, card.session_id)

    assert (stats.total, stats.correct, stats.partial, stats.incorrect) == (2, 1, 0, 1)
    assert stats.wrong_item_ids == (wrong.content_item_id,)

    async with database.session() as session:
        exercise_session = await session.get(ExerciseSession, card.session_id)
        assert exercise_session is not None
        assert exercise_session.state is ExerciseState.FINISHED

    await database.dispose()


async def test_active_card_restores_snapshot(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_vocabulary(database, count=10)
    user = await make_user(database)
    cards = service(database, card_size=4)
    card = await cards.start_card(user, direction=WordCardDirection.MIX)

    restored = await cards.active_card(user)

    assert restored is not None
    assert restored.session_id == card.session_id
    assert restored.item_ids == card.item_ids
    assert [question.direction for question in restored.questions] == [
        question.direction for question in card.questions
    ]

    await database.dispose()


async def test_cancel_active_abandons_card(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_vocabulary(database, count=10)
    user = await make_user(database)
    cards = service(database, card_size=2)
    card = await cards.start_card(user)

    stats = await cards.cancel_active(user)

    assert stats is not None
    assert stats.total == 0
    assert await cards.active_card(user) is None
    assert await cards.cancel_active(user) is None

    async with database.session() as session:
        exercise_session = await session.get(ExerciseSession, card.session_id)
        assert exercise_session is not None
        assert exercise_session.state is ExerciseState.ABANDONED

    await database.dispose()


async def test_foreign_user_cannot_submit(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed_vocabulary(database, count=10)
    owner = await make_user(database)
    other = await make_user(database, tg_id=200)
    cards = service(database, card_size=1)
    card = await cards.start_card(owner)
    question = card.questions[0]

    with pytest.raises(WordCardSessionError):
        await cards.submit_answer(
            other,
            session_id=card.session_id,
            content_item_id=question.content_item_id,
            direction=question.direction,
            answer="anything",
        )

    await database.dispose()


async def test_item_outside_card_is_rejected(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    item_ids = await seed_vocabulary(database, count=10)
    user = await make_user(database)
    cards = service(database, card_size=1)
    card = await cards.start_card(user)
    outside = next(item_id for item_id in item_ids if item_id not in card.item_ids)

    with pytest.raises(WordCardSessionError):
        await cards.submit_answer(
            user,
            session_id=card.session_id,
            content_item_id=outside,
            direction=WordDirection.EN_RU,
            answer="one",
        )

    await database.dispose()


async def test_start_card_without_content_fails(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)

    with pytest.raises(WordCardUnavailable):
        await service(database).start_card(user)

    await database.dispose()
