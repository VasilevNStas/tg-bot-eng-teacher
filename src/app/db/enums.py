"""Domain enums persisted in the database.

All enums are ``StrEnum`` and are stored as VARCHAR. Adding a value does not
require a migration on SQLite (see ``_db_schema.md``); the new value must be
handled in code before it is written.
"""

from enum import StrEnum


class CEFRLevel(StrEnum):
    """Coarse English proficiency level; also used by placement tests."""

    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


class ConversationMode(StrEnum):
    """What the current conversation is for."""

    CHAT = "chat"
    CORRECTION = "correction"
    DRILL = "drill"
    ROLEPLAY = "roleplay"


class MessageRole(StrEnum):
    """LLM message role, mirrors the chat completion API."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class MessageType(StrEnum):
    """Telegram message payload type; voice/image are reserved for later."""

    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"


class ContentKind(StrEnum):
    """Discriminator for the ``content_items.payload`` schema."""

    IRREGULAR_VERB = "irregular_verb"
    GAP_FILL = "gap_fill"
    TRANSLATION_PAIR = "translation_pair"
    WORD_ORDER = "word_order"
    MULTIPLE_CHOICE = "multiple_choice"
    VOCABULARY = "vocabulary"


class ExerciseState(StrEnum):
    """Lifecycle of an exercise session."""

    ACTIVE = "active"
    FINISHED = "finished"
    ABANDONED = "abandoned"


class ExerciseSessionKind(StrEnum):
    """Which practice flow created the session."""

    DRILL = "drill"
    WORDS = "words"


class WordDirection(StrEnum):
    """Card direction: English→Russian or Russian→English."""

    EN_RU = "en_ru"
    RU_EN = "ru_en"


class Verdict(StrEnum):
    """Grading outcome; ``partial`` usually means a tolerated typo."""

    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"


class Checker(StrEnum):
    """Who graded the answer: deterministic code or the LLM."""

    CODE = "code"
    LLM = "llm"


class ErrorCategory(StrEnum):
    """Buckets for chat corrections, used in statistics and prompts."""

    TENSE = "tense"
    ARTICLE = "article"
    PREPOSITION = "preposition"
    WORD_ORDER = "word_order"
    SPELLING = "spelling"
    VOCABULARY = "vocabulary"
    REGISTER = "register"


class ReminderKind(StrEnum):
    """Why a reminder was scheduled."""

    SRS = "srs"
    DAILY = "daily"
