"""Resolve the Telegram user into a persisted domain user."""

from __future__ import annotations

from aiogram.types import User as TgUser

from app.core.di import AppServices
from app.db.models import User
from app.db.repositories.users import UserRepository


async def resolve_user(services: AppServices, tg_user: TgUser) -> User:
    """Find or create the domain user and refresh ``last_seen_at``.

    Handlers call this on every update; the repository keeps the write cheap
    and merges profile defaults only on creation.
    """
    async with services.database.session() as session:
        repository = UserRepository(session)
        user = await repository.get_or_create(
            tg_id=tg_user.id,
            username=tg_user.username,
        )
        UserRepository.touch(user)
    return user
