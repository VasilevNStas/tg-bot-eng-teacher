from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_file
from app.db.base import Base
from app.db.database import Database
from app.db.enums import (
    Checker,
    ContentKind,
    ConversationMode,
    ErrorCategory,
    ExerciseSessionKind,
    ExerciseState,
    MessageRole,
    Verdict,
)
from app.db.models import (
    ContentItem,
    Conversation,
    Correction,
    ExerciseAttempt,
    ExerciseSession,
    Message,
    SrsReview,
    Topic,
    User,
    UserProgress,
    Vocabulary,
    WordProgress,
)
from app.services.stats.service import StatsService

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "sample.yaml"
VOCABULARY = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "vocabulary.yaml"


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def test_empty_stats(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    async with database.session() as session:
        user = User(tg_id=100)
        session.add(user)
        await session.flush()
        user_id = user.id

    report = await StatsService(database).overview(user_id, now=NOW)

    assert report.attempts_total == 0
    assert report.accuracy == 0
    assert report.completed_sessions == 0
    assert report.weak_topics == ()
    assert report.vocabulary_total == 0
    assert report.due_count == 0
    assert report.weak_words == ()
    assert report.error_categories == ()
    assert report.best_streak == 0
    assert report.word_sessions == 0
    assert report.word_attempts == 0
    assert report.word_accuracy == 0
    assert report.bank_words_seen == 0
    assert report.weak_bank_words == ()

    await database.dispose()


async def test_stats_aggregates_attempts_words_and_errors(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await ContentImporter(database).import_file(load_content_file(SAMPLE))

    async with database.session() as session:
        user = User(tg_id=100)
        session.add(user)
        await session.flush()

        topic = await session.scalar(select(Topic).where(Topic.slug == "test-topic-a1"))
        item = await session.scalar(select(ContentItem).limit(1))
        assert topic is not None
        assert item is not None

        finished = ExerciseSession(
            user_id=user.id,
            topic_id=topic.id,
            state=ExerciseState.FINISHED,
        )
        active = ExerciseSession(user_id=user.id, topic_id=topic.id)
        session.add_all([finished, active])
        await session.flush()

        session.add_all(
            [
                ExerciseAttempt(
                    user_id=user.id,
                    session_id=finished.id,
                    content_item_id=item.id,
                    answer="went",
                    verdict=Verdict.CORRECT,
                    checker=Checker.CODE,
                ),
                ExerciseAttempt(
                    user_id=user.id,
                    session_id=finished.id,
                    content_item_id=item.id,
                    answer="go",
                    verdict=Verdict.INCORRECT,
                    checker=Checker.CODE,
                ),
                ExerciseAttempt(
                    user_id=user.id,
                    session_id=active.id,
                    content_item_id=item.id,
                    answer="wnt",
                    verdict=Verdict.PARTIAL,
                    checker=Checker.CODE,
                ),
            ]
        )
        session.add(
            UserProgress(
                user_id=user.id,
                topic_id=topic.id,
                mastery=0.5,
                streak=4,
            )
        )

        weak = Vocabulary(user_id=user.id, term="cinema", translation="кинотеатр")
        new = Vocabulary(user_id=user.id, term="travel", translation="путешествовать")
        future = Vocabulary(user_id=user.id, term="work", translation="работа")
        session.add_all([weak, new, future])
        await session.flush()
        session.add_all(
            [
                SrsReview(
                    vocabulary_id=weak.id,
                    due_at=NOW - timedelta(days=1),
                    lapses=2,
                    ease=2.0,
                ),
                SrsReview(
                    vocabulary_id=future.id,
                    due_at=NOW + timedelta(days=2),
                    lapses=0,
                    ease=2.5,
                ),
            ]
        )

        conversation = Conversation(user_id=user.id, mode=ConversationMode.CORRECTION)
        session.add(conversation)
        await session.flush()
        message = Message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="I go yesterday",
        )
        session.add(message)
        await session.flush()
        session.add_all(
            [
                Correction(
                    message_id=message.id,
                    original="go",
                    corrected="went",
                    category=ErrorCategory.TENSE,
                    explanation="Past Simple",
                ),
                Correction(
                    message_id=message.id,
                    original="I",
                    corrected="I",
                    category=ErrorCategory.TENSE,
                    explanation="test",
                ),
                Correction(
                    message_id=message.id,
                    original="a",
                    corrected="the",
                    category=ErrorCategory.ARTICLE,
                    explanation="test",
                ),
            ]
        )
        user_id = user.id

    report = await StatsService(database).overview(user_id, now=NOW)

    assert report.attempts_total == 3
    assert (report.correct, report.partial, report.incorrect) == (1, 1, 1)
    assert report.accuracy == 33
    assert report.completed_sessions == 1
    assert report.best_streak == 4
    assert len(report.weak_topics) == 1
    assert report.weak_topics[0].title == "Test topic A1"
    assert report.weak_topics[0].accuracy == 33
    assert report.vocabulary_total == 3
    assert report.due_count == 2
    assert report.weak_words[0].term == "cinema"
    assert report.weak_words[0].lapses == 2
    assert [(item.category, item.count) for item in report.error_categories] == [
        (ErrorCategory.TENSE, 2),
        (ErrorCategory.ARTICLE, 1),
    ]

    await database.dispose()


async def test_stats_includes_word_cards(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await ContentImporter(database).import_file(load_content_file(VOCABULARY))

    async with database.session() as session:
        user = User(tg_id=100)
        session.add(user)
        await session.flush()

        items = list(
            await session.scalars(
                select(ContentItem)
                .where(ContentItem.kind == ContentKind.VOCABULARY)
                .order_by(ContentItem.id)
            )
        )
        assert len(items) == 3

        card = ExerciseSession(
            user_id=user.id,
            kind=ExerciseSessionKind.WORDS,
            state=ExerciseState.FINISHED,
            meta={"item_ids": [items[0].id, items[2].id]},
        )
        session.add(card)
        await session.flush()
        session.add_all(
            [
                ExerciseAttempt(
                    user_id=user.id,
                    session_id=card.id,
                    content_item_id=items[0].id,
                    answer="оставить",
                    verdict=Verdict.CORRECT,
                    checker=Checker.CODE,
                ),
                ExerciseAttempt(
                    user_id=user.id,
                    session_id=card.id,
                    content_item_id=items[2].id,
                    answer="zzz",
                    verdict=Verdict.INCORRECT,
                    checker=Checker.CODE,
                ),
                ExerciseAttempt(
                    user_id=user.id,
                    session_id=card.id,
                    content_item_id=items[0].id,
                    answer="zzz",
                    verdict=Verdict.INCORRECT,
                    checker=Checker.CODE,
                ),
            ]
        )
        session.add_all(
            [
                WordProgress(
                    user_id=user.id,
                    content_item_id=items[0].id,
                    shown_count=2,
                    attempt_count=2,
                    correct_count=1,
                    wrong_count=1,
                ),
                WordProgress(
                    user_id=user.id,
                    content_item_id=items[2].id,
                    shown_count=1,
                    attempt_count=1,
                    wrong_count=1,
                ),
            ]
        )
        user_id = user.id

    report = await StatsService(database).overview(user_id, now=NOW)

    assert report.word_sessions == 1
    assert report.word_attempts == 3
    assert report.word_correct == 1
    assert report.word_accuracy == 33
    assert report.bank_words_seen == 2
    assert [(word.term, word.wrong, word.attempts) for word in report.weak_bank_words] == [
        ("abandon", 1, 2),
        ("care", 1, 1),
    ]

    await database.dispose()
