"""ORM models."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.annotations import (
    bool_default_true,
    int_pk,
    json_dict_not_null,
    str_64,
    str_128,
    str_enum,
    text,
    tg_id,
)
from app.db.base import Base, CreatedAtMixin, TimestampMixin
from app.db.enums import (
    CEFRLevel,
    Checker,
    ContentKind,
    ConversationMode,
    ErrorCategory,
    ExerciseSessionKind,
    ExerciseState,
    MessageRole,
    MessageType,
    ReminderKind,
    Verdict,
    WordDirection,
)


def _now() -> datetime:
    return datetime.now(UTC)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int_pk]
    tg_id: Mapped[tg_id]
    username: Mapped[str | None] = mapped_column(String(64))
    native_lang: Mapped[str] = mapped_column(String(8), default="ru", nullable=False)
    level: Mapped[CEFRLevel] = mapped_column(default=CEFRLevel.A2, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    quiet_hours_start: Mapped[str | None] = mapped_column(String(5))
    quiet_hours_end: Mapped[str | None] = mapped_column(String(5))
    onboarding_state: Mapped[str] = mapped_column(String(32), default="new", nullable=False)
    profile: Mapped[json_dict_not_null]
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[int_pk]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    mode: Mapped[ConversationMode] = mapped_column(default=ConversationMode.CHAT, nullable=False)
    topic: Mapped[str | None] = mapped_column(String(128))
    summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int_pk]
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(nullable=False)
    message_type: Mapped[MessageType] = mapped_column(default=MessageType.TEXT, nullable=False)
    content: Mapped[text]
    input_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    update_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class ProcessedUpdate(Base):
    __tablename__ = "processed_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class Topic(TimestampMixin, Base):
    __tablename__ = "topics"

    id: Mapped[int_pk]
    slug: Mapped[str_64] = mapped_column(unique=True, index=True)
    title: Mapped[str_128]
    level: Mapped[CEFRLevel] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class ContentItem(TimestampMixin, Base):
    __tablename__ = "content_items"

    id: Mapped[int_pk]
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    variant_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="SET NULL"), index=True
    )
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    is_active: Mapped[bool_default_true]
    kind: Mapped[ContentKind] = mapped_column(nullable=False)
    level: Mapped[CEFRLevel] = mapped_column(nullable=False)
    payload: Mapped[json_dict_not_null]
    source: Mapped[str | None] = mapped_column(String(256))
    license: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)


class TopicItem(Base):
    __tablename__ = "topic_items"

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    position: Mapped[int] = mapped_column(default=0, nullable=False)


class ContentItemTag(Base):
    __tablename__ = "content_item_tags"

    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)


class ExerciseSession(TimestampMixin, Base):
    __tablename__ = "exercise_sessions"

    id: Mapped[int_pk]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[ExerciseSessionKind] = mapped_column(
        str_enum(ExerciseSessionKind),
        default=ExerciseSessionKind.DRILL,
        nullable=False,
    )
    meta: Mapped[json_dict_not_null]
    state: Mapped[ExerciseState] = mapped_column(default=ExerciseState.ACTIVE, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExerciseAttempt(CreatedAtMixin, Base):
    __tablename__ = "exercise_attempts"

    id: Mapped[int_pk]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("exercise_sessions.id", ondelete="SET NULL"), index=True
    )
    answer: Mapped[text]
    verdict: Mapped[Verdict] = mapped_column(nullable=False)
    checker: Mapped[Checker] = mapped_column(nullable=False)
    direction: Mapped[WordDirection | None] = mapped_column(str_enum(WordDirection))
    latency_ms: Mapped[int | None]


class Correction(CreatedAtMixin, Base):
    __tablename__ = "corrections"

    id: Mapped[int_pk]
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    original: Mapped[text]
    corrected: Mapped[text]
    category: Mapped[ErrorCategory] = mapped_column(index=True, nullable=False)
    explanation: Mapped[text]


class UserProgress(TimestampMixin, Base):
    __tablename__ = "user_progress"
    __table_args__ = (UniqueConstraint("user_id", "topic_id", name="uq_user_progress_user_topic"),)

    id: Mapped[int_pk]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    mastery: Mapped[float] = mapped_column(default=0.0, nullable=False)
    streak: Mapped[int] = mapped_column(default=0, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Vocabulary(TimestampMixin, Base):
    __tablename__ = "vocabulary"
    __table_args__ = (UniqueConstraint("user_id", "term", name="uq_vocabulary_user_term"),)

    id: Mapped[int_pk]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    term: Mapped[str_128]
    translation: Mapped[str] = mapped_column(String(256), nullable=False)
    context: Mapped[str | None] = mapped_column(Text)
    source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), index=True
    )


class SrsReview(TimestampMixin, Base):
    __tablename__ = "srs_reviews"

    id: Mapped[int_pk]
    vocabulary_id: Mapped[int] = mapped_column(
        ForeignKey("vocabulary.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    interval_days: Mapped[int] = mapped_column(default=0, nullable=False)
    ease: Mapped[float] = mapped_column(default=2.5, nullable=False)
    reps: Mapped[int] = mapped_column(default=0, nullable=False)
    lapses: Mapped[int] = mapped_column(default=0, nullable=False)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WordProgress(TimestampMixin, Base):
    __tablename__ = "word_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "content_item_id", name="uq_word_progress_user_item"),
        Index("ix_word_progress_user_due_at", "user_id", "next_due_at"),
    )

    id: Mapped[int_pk]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    shown_count: Mapped[int] = mapped_column(default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    correct_count: Mapped[int] = mapped_column(default=0, nullable=False)
    partial_count: Mapped[int] = mapped_column(default=0, nullable=False)
    wrong_count: Mapped[int] = mapped_column(default=0, nullable=False)
    streak: Mapped[int] = mapped_column(default=0, nullable=False)
    best_streak: Mapped[int] = mapped_column(default=0, nullable=False)
    last_shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageLedger(CreatedAtMixin, Base):
    __tablename__ = "usage_ledger"

    id: Mapped[int_pk]
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"), nullable=False)
    latency_ms: Mapped[int | None]


class Reminder(TimestampMixin, Base):
    __tablename__ = "reminders"

    id: Mapped[int_pk]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    kind: Mapped[ReminderKind] = mapped_column(nullable=False)
    payload: Mapped[json_dict_not_null]
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(CreatedAtMixin, Base):
    __tablename__ = "events"

    id: Mapped[int_pk]
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str_64] = mapped_column(index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
