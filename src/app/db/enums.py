"""Domain enums persisted in the database."""

from enum import StrEnum


class CEFRLevel(StrEnum):
    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


class ConversationMode(StrEnum):
    CHAT = "chat"
    CORRECTION = "correction"
    DRILL = "drill"
    ROLEPLAY = "roleplay"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class MessageType(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"


class ContentKind(StrEnum):
    IRREGULAR_VERB = "irregular_verb"
    GAP_FILL = "gap_fill"
    TRANSLATION_PAIR = "translation_pair"
    WORD_ORDER = "word_order"
    MULTIPLE_CHOICE = "multiple_choice"


class ExerciseState(StrEnum):
    ACTIVE = "active"
    FINISHED = "finished"
    ABANDONED = "abandoned"


class Verdict(StrEnum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"


class Checker(StrEnum):
    CODE = "code"
    LLM = "llm"


class ErrorCategory(StrEnum):
    TENSE = "tense"
    ARTICLE = "article"
    PREPOSITION = "preposition"
    WORD_ORDER = "word_order"
    SPELLING = "spelling"
    VOCABULARY = "vocabulary"
    REGISTER = "register"


class ReminderKind(StrEnum):
    SRS = "srs"
    DAILY = "daily"
