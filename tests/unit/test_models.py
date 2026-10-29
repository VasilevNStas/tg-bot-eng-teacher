from __future__ import annotations

from pathlib import Path

from app.db.base import Base
from app.db.database import Database
from app.db.enums import CEFRLevel, ConversationMode
from app.db.models import Conversation, User

EXPECTED_TABLES = {
    "users",
    "conversations",
    "messages",
    "processed_updates",
    "topics",
    "content_items",
    "topic_items",
    "content_item_tags",
    "exercise_sessions",
    "exercise_attempts",
    "corrections",
    "user_progress",
    "vocabulary",
    "srs_reviews",
    "usage_ledger",
    "reminders",
    "events",
    "word_progress",
}


def test_metadata_contains_expected_tables() -> None:
    assert set(Base.metadata.tables) >= EXPECTED_TABLES


async def test_user_and_conversation_roundtrip(tmp_path: Path) -> None:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with database.session() as session:
        user = User(tg_id=100500, level=CEFRLevel.B1, profile={"goal": "interview"})
        session.add(user)
        await session.flush()

        conversation = Conversation(user_id=user.id, mode=ConversationMode.CHAT)
        session.add(conversation)
        await session.flush()

    async with database.session() as session:
        stored_user = await session.get(User, user.id)
        assert stored_user is not None
        assert stored_user.tg_id == 100500
        assert stored_user.level is CEFRLevel.B1
        assert stored_user.profile == {"goal": "interview"}
        assert stored_user.created_at is not None

        stored_conversation = await session.get(Conversation, conversation.id)
        assert stored_conversation is not None
        assert stored_conversation.mode is ConversationMode.CHAT
        assert stored_conversation.ended_at is None

    await database.dispose()
