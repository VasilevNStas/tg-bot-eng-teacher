from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.db.base import Base
from app.db.database import Database
from app.db.enums import (
    CEFRLevel,
    Checker,
    ContentKind,
    ConversationMode,
    ErrorCategory,
    MessageRole,
    ReminderKind,
    Verdict,
)
from app.db.models import (
    ContentItem,
    Conversation,
    Correction,
    Event,
    ExerciseAttempt,
    ExerciseSession,
    Message,
    Reminder,
    SrsReview,
    Topic,
    UsageLedger,
    User,
    UserProgress,
    Vocabulary,
)
from app.services.privacy.service import PrivacyService


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def count(database: Database, model: type[object]) -> int:
    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


async def test_delete_user_removes_all_owned_data(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    now = datetime.now(UTC)
    async with database.session() as session:
        user = User(tg_id=100, username="tester")
        session.add(user)
        await session.flush()

        topic = Topic(slug="test-topic", title="Test", level=CEFRLevel.A1, tags=[])
        content = ContentItem(
            slug="test-item",
            kind=ContentKind.MULTIPLE_CHOICE,
            level=CEFRLevel.A1,
            payload={
                "kind": "multiple_choice",
                "question": "I ___ here.",
                "options": ["am", "is"],
                "correct_index": 0,
            },
        )
        conversation = Conversation(user_id=user.id, mode=ConversationMode.CHAT)
        session.add_all([topic, content, conversation])
        await session.flush()

        message = Message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="hello",
        )
        word = Vocabulary(
            user_id=user.id,
            term="cinema",
            translation="кинотеатр",
        )
        exercise_session = ExerciseSession(user_id=user.id, topic_id=topic.id)
        session.add_all([message, word, exercise_session])
        await session.flush()

        session.add_all(
            [
                Correction(
                    message_id=message.id,
                    original="I go",
                    corrected="I went",
                    category=ErrorCategory.TENSE,
                    explanation="Past Simple",
                ),
                SrsReview(vocabulary_id=word.id, due_at=now),
                ExerciseAttempt(
                    user_id=user.id,
                    content_item_id=content.id,
                    session_id=exercise_session.id,
                    answer="went",
                    verdict=Verdict.CORRECT,
                    checker=Checker.CODE,
                ),
                Reminder(user_id=user.id, due_at=now, kind=ReminderKind.SRS, payload={}),
                UsageLedger(user_id=user.id, conversation_id=conversation.id, model="fake"),
                Event(user_id=user.id, kind="test", payload={"secret": "value"}),
                UserProgress(user_id=user.id, topic_id=topic.id),
            ]
        )

    summary = await PrivacyService(database).delete_user(100)

    assert summary is not None
    assert summary.conversations == 1
    assert summary.messages == 1
    assert summary.corrections == 1
    assert summary.vocabulary == 1
    assert summary.sessions == 1
    assert summary.attempts == 1
    assert summary.reminders == 1
    assert summary.usage == 1
    assert summary.events == 1

    assert await count(database, User) == 0
    assert await count(database, Conversation) == 0
    assert await count(database, Message) == 0
    assert await count(database, Correction) == 0
    assert await count(database, Vocabulary) == 0
    assert await count(database, SrsReview) == 0
    assert await count(database, ExerciseSession) == 0
    assert await count(database, ExerciseAttempt) == 0
    assert await count(database, Reminder) == 0
    assert await count(database, UsageLedger) == 0
    assert await count(database, Event) == 0
    assert await count(database, UserProgress) == 0
    assert await count(database, ContentItem) == 1
    assert await count(database, Topic) == 1

    assert await PrivacyService(database).delete_user(100) is None
    await database.dispose()


async def test_prune_history_keeps_recent_conversations(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    now = datetime.now(UTC)
    old = now - timedelta(days=100)
    async with database.session() as session:
        user = User(tg_id=100, username="tester")
        session.add(user)
        await session.flush()

        old_ended = Conversation(
            user_id=user.id,
            mode=ConversationMode.CHAT,
            started_at=old,
            ended_at=old + timedelta(hours=1),
        )
        old_active = Conversation(
            user_id=user.id,
            mode=ConversationMode.CHAT,
            started_at=old,
        )
        recent = Conversation(
            user_id=user.id,
            mode=ConversationMode.CHAT,
            started_at=now - timedelta(days=10),
        )
        session.add_all([old_ended, old_active, recent])
        await session.flush()
        session.add_all(
            [
                Message(conversation_id=old_ended.id, role=MessageRole.USER, content="old"),
                Message(conversation_id=old_active.id, role=MessageRole.USER, content="old"),
                Message(conversation_id=recent.id, role=MessageRole.USER, content="new"),
            ]
        )

    deleted = await PrivacyService(database).prune_history(90, now=now)

    assert deleted == 2
    assert await count(database, Conversation) == 1
    assert await count(database, Message) == 1
    async with database.session() as session:
        remaining = (await session.scalars(select(Message))).one()
        assert remaining.content == "new"
    await database.dispose()
