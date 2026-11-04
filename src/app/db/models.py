"""ORM models.

Schema overview (14 tables):

- identity/chat: ``users``, ``conversations``, ``messages``, ``processed_updates``
- content:      ``topics``, ``content_items``
- practice:     ``exercise_sessions``, ``exercise_attempts``, ``corrections``
- learning:     ``vocabulary`` (saved words + SRS), ``word_progress``
- operations:   ``usage_ledger``, ``reminders``, ``admin_audit``

Quiz content lives in ``content_items.payload`` as JSON; its shape depends on
the ``kind`` column. User-owned rows cascade from ``users`` so deleting a
profile removes everything the user produced.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
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
    str_32,
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
    """Timezone-aware UTC default used by timestamp columns."""
    return datetime.now(UTC)


class User(TimestampMixin, Base):
    """Telegram user and learning preferences.

    ``profile`` is a JSON bag for feature settings (word card size/direction)
    so adding an option does not require a migration. ``onboarding_state``
    tracks the FSM position by name rather than aiogram state objects.
    """

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

    # Containment state written by the admin bot and enforced by the user bot.
    # ``blocked_at`` is a permanent block; ``muted_until`` expires on its own.
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_reason: Mapped[str | None] = mapped_column(String(256))
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mute_reason: Mapped[str | None] = mapped_column(String(256))


class Conversation(TimestampMixin, Base):
    """A chat or roleplay session; messages belong to one conversation."""

    __tablename__ = "conversations"

    id: Mapped[int_pk]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    mode: Mapped[ConversationMode] = mapped_column(default=ConversationMode.CHAT, nullable=False)
    topic: Mapped[str | None] = mapped_column(String(128))
    # Rolling summary used to keep the LLM context small for long chats.
    summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    """One message in a conversation.

    Token usage and cost are not stored here; they live in ``usage_ledger`` so
    analytics can track LLM calls (including retries) independently.
    """

    __tablename__ = "messages"

    id: Mapped[int_pk]
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(nullable=False)
    message_type: Mapped[MessageType] = mapped_column(default=MessageType.TEXT, nullable=False)
    content: Mapped[text]
    # Telegram update id, when known, for tracing and deduplication.
    update_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class ProcessedUpdate(Base):
    """Telegram update ids that have already been handled.

    Polling can redeliver an update after a restart; this table makes handlers
    idempotent by letting the middleware skip a known ``update_id``.
    """

    __tablename__ = "processed_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class Topic(TimestampMixin, Base):
    """A named group of content items (a unit, a word block, a drill set)."""

    __tablename__ = "topics"

    id: Mapped[int_pk]
    slug: Mapped[str_64] = mapped_column(unique=True, index=True)
    title: Mapped[str_128]


class ContentItem(TimestampMixin, Base):
    """A single learning unit: word, verb, gap-fill, question, etc.

    ``slug`` is the identity used by the idempotent importer. ``kind`` selects
    how ``payload`` is validated and interpreted; ``payload`` never repeats
    the kind, so the database stays readable.
    """

    __tablename__ = "content_items"

    id: Mapped[int_pk]
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    is_active: Mapped[bool_default_true]
    kind: Mapped[ContentKind] = mapped_column(nullable=False)
    payload: Mapped[json_dict_not_null]
    source: Mapped[str | None] = mapped_column(String(256))
    license: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)


class ExerciseSession(TimestampMixin, Base):
    """A run of a drill or word-card session.

    ``meta`` stores the session snapshot (selected item ids, directions) so a
    session can be restored after a restart without re-randomizing.
    """

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
    """A single graded answer; the source of truth for accuracy stats."""

    __tablename__ = "exercise_attempts"

    id: Mapped[int_pk]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # RESTRICT: content with attempts stays in the database for statistics.
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("exercise_sessions.id", ondelete="SET NULL"), index=True
    )
    answer: Mapped[text]
    verdict: Mapped[Verdict] = mapped_column(nullable=False)
    checker: Mapped[Checker] = mapped_column(nullable=False)
    # Set only for word cards: which direction the user was answering.
    direction: Mapped[WordDirection | None] = mapped_column(str_enum(WordDirection))
    latency_ms: Mapped[int | None]


class Correction(CreatedAtMixin, Base):
    """An error correction produced from a chat message."""

    __tablename__ = "corrections"

    id: Mapped[int_pk]
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    original: Mapped[text]
    corrected: Mapped[text]
    category: Mapped[ErrorCategory] = mapped_column(index=True, nullable=False)
    explanation: Mapped[text]


class Vocabulary(TimestampMixin, Base):
    """A word saved by the user, with its own SRS scheduling state.

    This is the "my words" list captured from chat. Bank words are practiced
    through ``word_progress`` instead; the two systems are intentionally
    separate because one is user-curated and the other is course content.
    """

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
    # SRS state (simplified SM-2): NULL due_at means "not scheduled yet".
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    interval_days: Mapped[int] = mapped_column(default=0, nullable=False)
    ease: Mapped[float] = mapped_column(default=2.5, nullable=False)
    reps: Mapped[int] = mapped_column(default=0, nullable=False)
    lapses: Mapped[int] = mapped_column(default=0, nullable=False)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WordProgress(TimestampMixin, Base):
    """Per-user statistics and scheduling for one content-bank word.

    The unique constraint guarantees exactly one row per user/item pair; the
    composite index serves the "due words for this user" query.
    """

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
    """One LLM call: tokens, cost, latency and the prompt version used.

    Rows are kept when a user or conversation is deleted (SET NULL) so cost
    accounting survives profile deletion.
    """

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
    """A scheduled notification; ``sent_at`` is NULL until it is delivered."""

    __tablename__ = "reminders"

    id: Mapped[int_pk]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    kind: Mapped[ReminderKind] = mapped_column(nullable=False)
    payload: Mapped[json_dict_not_null]
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminAudit(CreatedAtMixin, Base):
    """One administrative action, written by the admin bot or the CLI.

    ``target_tg_id`` is not a foreign key on purpose: an audit record must
    survive deletion of the user it refers to. ``details`` keeps action-specific
    data (reason, duration, counts) without schema changes.
    """

    __tablename__ = "admin_audit"
    __table_args__ = (Index("ix_admin_audit_created_at", "created_at"),)

    id: Mapped[int_pk]
    actor: Mapped[str_128]
    source: Mapped[str_32]
    action: Mapped[str_32]
    target_tg_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    details: Mapped[json_dict_not_null]
    success: Mapped[bool_default_true]
