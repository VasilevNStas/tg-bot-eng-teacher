"""Read and persist the ``goals`` section of the JSON user profile."""

from __future__ import annotations

from app.db.database import Database
from app.db.models import User
from app.db.repositories.users import UserRepository
from app.services.goals.model import PROFILE_SECTION, GoalsProfile


class GoalsService:
    """Thin persistence layer around the profile's ``goals`` section."""

    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def read(user: User) -> GoalsProfile:
        """Read the current goals without touching the database."""
        return GoalsProfile.from_profile(user.profile)

    async def save(self, user: User, profile: GoalsProfile) -> bool:
        """Merge the goals section, preserving other profile features."""
        async with self._database.session() as session:
            return await UserRepository(session).update_profile_section(
                user.tg_id,
                PROFILE_SECTION,
                profile.to_section(),
            )
