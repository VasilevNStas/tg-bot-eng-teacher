"""Typed callback data."""

from __future__ import annotations

from typing import Literal

from aiogram.filters.callback_data import CallbackData


class OnboardingCB(CallbackData, prefix="onb"):
    action: Literal["test", "skip", "answer", "level"]
    index: int = 0
    value: str = ""
