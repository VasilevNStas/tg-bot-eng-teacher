"""Personal learning goals stored in the JSON user profile."""

from app.services.goals.model import (
    DAILY_OPTIONS,
    DEFAULT_DAILY_ITEMS,
    GOAL_OPTIONS,
    INTEREST_OPTIONS,
    PROFILE_SECTION,
    GoalsProfile,
    render_prompt_block,
)
from app.services.goals.service import GoalsService

__all__ = [
    "DAILY_OPTIONS",
    "DEFAULT_DAILY_ITEMS",
    "GOAL_OPTIONS",
    "INTEREST_OPTIONS",
    "PROFILE_SECTION",
    "GoalsProfile",
    "GoalsService",
    "render_prompt_block",
]
