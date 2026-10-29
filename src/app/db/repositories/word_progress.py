"""Per-word aggregate progress for vocabulary cards."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import Verdict
from app.db.models import WordProgress
from app.db.repository import BaseRepository


class WordProgressRepository(BaseRepository[WordProgress]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, WordProgress)

    async def get_for(self, user_id: int, content_item_id: int) -> WordProgress | None:
        return await self._session.scalar(
            select(WordProgress).where(
                WordProgress.user_id == user_id,
                WordProgress.content_item_id == content_item_id,
            )
        )

    async def get_or_create(self, user_id: int, content_item_id: int) -> WordProgress:
        progress = await self.get_for(user_id, content_item_id)
        if progress is None:
            progress = WordProgress(
                user_id=user_id,
                content_item_id=content_item_id,
            )
            self._session.add(progress)
            await self._session.flush()
        return progress

    async def mark_shown(self, progress: WordProgress, at: datetime) -> None:
        progress.shown_count += 1
        progress.last_shown_at = at

    async def record_attempt(
        self,
        progress: WordProgress,
        verdict: Verdict,
        at: datetime,
    ) -> None:
        progress.attempt_count += 1
        progress.last_attempted_at = at
        if verdict is Verdict.CORRECT:
            progress.correct_count += 1
            progress.streak += 1
            progress.best_streak = max(progress.best_streak, progress.streak)
        else:
            if verdict is Verdict.PARTIAL:
                progress.partial_count += 1
            else:
                progress.wrong_count += 1
            progress.streak = 0
        await self._session.flush()
