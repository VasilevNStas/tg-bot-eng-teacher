"""Typed callback data."""

from __future__ import annotations

from typing import Literal

from aiogram.filters.callback_data import CallbackData


class OnboardingCB(CallbackData, prefix="onb"):
    action: Literal["test", "skip", "answer", "level"]
    index: int = 0
    value: str = ""


class ReviewCB(CallbackData, prefix="rev"):
    action: Literal["again", "good", "easy"]
    vocabulary_id: int


class RoleplayCB(CallbackData, prefix="rp"):
    action: Literal["start"]
    slug: str


class DeleteCB(CallbackData, prefix="del"):
    action: Literal["confirm", "cancel"]


class WordsCB(CallbackData, prefix="wrd"):
    action: Literal["quiz", "settings", "size", "direction", "retry", "new"]
    value: str = ""
