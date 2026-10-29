"""User lookup and creation."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import CEFRLevel
from app.db.models import User
from app.db.repository import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    async def get_by_tg_id(self, tg_id: int) -> User | None:
        return await self._session.scalar(select(User).where(User.tg_id == tg_id))

    async def get_or_create(
        self,
        *,
        tg_id: int,
        username: str | None,
        native_lang: str = "ru",
    ) -> User:
        user = await self.get_by_tg_id(tg_id)
        if user is None:
            user = User(
                tg_id=tg_id,
                username=username,
                native_lang=native_lang,
                level=CEFRLevel.A2,
                timezone="UTC",
                onboarding_state="new",
                profile={},
            )
            self._session.add(user)
            await self._session.flush()
        elif username is not None and user.username != username:
            user.username = username
        return user

    async def update_level(self, tg_id: int, level: CEFRLevel) -> bool:
        user = await self.get_by_tg_id(tg_id)
        if user is None:
            return False
        user.level = level
        user.onboarding_state = "done"
        return True

    @staticmethod
    def touch(user: User, at: datetime | None = None) -> None:
        user.last_seen_at = at or datetime.now(UTC)
