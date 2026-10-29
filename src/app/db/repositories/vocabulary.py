"""User vocabulary lookup and persistence."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Vocabulary
from app.db.repository import BaseRepository


class VocabularyRepository(BaseRepository[Vocabulary]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Vocabulary)

    async def list_for_user(self, user_id: int, *, limit: int = 50) -> list[Vocabulary]:
        return await self._scalars(
            select(Vocabulary)
            .where(Vocabulary.user_id == user_id)
            .order_by(Vocabulary.created_at.desc(), Vocabulary.id.desc())
            .limit(limit)
        )

    async def save_many(
        self,
        user_id: int,
        items: Sequence[tuple[str, str, str | None]],
        *,
        source_message_id: int | None = None,
    ) -> int:
        terms = {
            term.casefold()
            for term in await self._session.scalars(
                select(Vocabulary.term).where(Vocabulary.user_id == user_id)
            )
        }
        added = 0
        for term, translation, context in items:
            clean_term = term.strip()[:128]
            clean_translation = translation.strip()[:256]
            clean_context = context.strip()[:2000] if context else None
            if not clean_term or not clean_translation:
                continue
            key = clean_term.casefold()
            if key in terms:
                continue
            self._session.add(
                Vocabulary(
                    user_id=user_id,
                    term=clean_term,
                    translation=clean_translation,
                    context=clean_context or None,
                    source_message_id=source_message_id,
                )
            )
            terms.add(key)
            added += 1
        if added:
            await self._session.flush()
        return added
