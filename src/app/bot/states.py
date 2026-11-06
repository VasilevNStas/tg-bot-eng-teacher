"""FSM states.

States keep multi-step flows (onboarding, a drill, a word card) attached to the
chat. The concrete step data lives in FSM storage and is treated as untrusted
when handlers read it back.
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    """Placement test and manual level choice."""

    testing = State()
    choosing_level = State()


class Drill(StatesGroup):
    """One irregular-verb drill session."""

    active = State()


class Words(StatesGroup):
    """Word-card study and quiz sessions."""

    study = State()
    quiz = State()


class Translation(StatesGroup):
    """One Russian-to-English translation practice session."""

    active = State()
