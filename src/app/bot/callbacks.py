"""Typed callback data.

Using ``CallbackData`` instead of raw strings gives compile-time-safe handlers
and keeps callback payloads under Telegram's 64-byte limit.
"""

from __future__ import annotations

from typing import Literal

from aiogram.filters.callback_data import CallbackData


class OnboardingCB(CallbackData, prefix="onb"):
    """Placement-test actions and answers."""

    action: Literal["test", "skip", "answer", "level"]
    index: int = 0
    value: str = ""


class ReviewCB(CallbackData, prefix="rev"):
    """Grade for a saved-vocabulary SRS card."""

    action: Literal["again", "good", "easy"]
    vocabulary_id: int


class RoleplayCB(CallbackData, prefix="rp"):
    """Start a role-play scenario by its prompt slug."""

    action: Literal["start"]
    slug: str


class DeleteCB(CallbackData, prefix="del"):
    """Confirm or cancel account deletion."""

    action: Literal["confirm", "cancel"]


class WordsCB(CallbackData, prefix="wrd"):
    """Word-card navigation and settings actions."""

    action: Literal["quiz", "settings", "size", "direction", "retry", "new"]
    value: str = ""


class MenuCB(CallbackData, prefix="menu"):
    """Main menu navigation and action shortcuts."""

    target: Literal[
        "root",
        "learn",
        "practice",
        "progress",
        "settings",
        "help",
        "drill",
        "words",
        "review",
        "vocab",
        "correction",
        "roleplay",
        "stop",
        "stats",
        "timezone",
        "quiet",
        "about",
        "delete",
    ]


class TaskCB(CallbackData, prefix="task"):
    """Actions shown directly under an active task."""

    action: Literal["cancel"]
