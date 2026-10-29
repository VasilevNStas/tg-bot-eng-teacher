"""FSM states."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    testing = State()
    choosing_level = State()


class Drill(StatesGroup):
    active = State()


class Words(StatesGroup):
    study = State()
    quiz = State()
