"""Exercise sessions, attempts and per-topic progress."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import Checker, ExerciseSessionKind, ExerciseState, Verdict, WordDirection
from app.db.models import ExerciseAttempt, ExerciseSession, UserProgress
from app.db.repository import BaseRepository


class ExerciseRepository(BaseRepository[ExerciseSession]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ExerciseSession)

    async def get_for_user(
        self,
        session_id: int,
        user_id: int,
    ) -> ExerciseSession | None:
        return await self._session.scalar(
            select(ExerciseSession).where(
                ExerciseSession.id == session_id,
                ExerciseSession.user_id == user_id,
            )
        )

    async def get_active(
        self,
        user_id: int,
        *,
        kind: ExerciseSessionKind | None = None,
    ) -> ExerciseSession | None:
        stmt = select(ExerciseSession).where(
            ExerciseSession.user_id == user_id,
            ExerciseSession.state == ExerciseState.ACTIVE,
        )
        if kind is not None:
            stmt = stmt.where(ExerciseSession.kind == kind)
        return await self._session.scalar(stmt.order_by(ExerciseSession.started_at.desc()).limit(1))

    async def abandon_active(
        self,
        user_id: int,
        at: datetime,
        *,
        kind: ExerciseSessionKind | None = None,
    ) -> int:
        stmt = select(ExerciseSession).where(
            ExerciseSession.user_id == user_id,
            ExerciseSession.state == ExerciseState.ACTIVE,
        )
        if kind is not None:
            stmt = stmt.where(ExerciseSession.kind == kind)
        sessions = await self._scalars(stmt)
        for item in sessions:
            item.state = ExerciseState.ABANDONED
            item.finished_at = at
        return len(sessions)

    async def finish(
        self,
        exercise_session: ExerciseSession,
        state: ExerciseState,
        at: datetime,
    ) -> None:
        if exercise_session.state is ExerciseState.ACTIVE:
            exercise_session.state = state
            exercise_session.finished_at = at

    async def add_attempt(
        self,
        *,
        user_id: int,
        content_item_id: int,
        session_id: int,
        answer: str,
        verdict: Verdict,
        checker: Checker,
        latency_ms: int | None,
        direction: WordDirection | None = None,
    ) -> ExerciseAttempt:
        attempt = ExerciseAttempt(
            user_id=user_id,
            content_item_id=content_item_id,
            session_id=session_id,
            answer=answer,
            verdict=verdict,
            checker=checker,
            latency_ms=latency_ms,
            direction=direction,
        )
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def counts(self, session_id: int) -> dict[Verdict, int]:
        rows = await self._session.execute(
            select(ExerciseAttempt.verdict, func.count())
            .where(ExerciseAttempt.session_id == session_id)
            .group_by(ExerciseAttempt.verdict)
        )
        return {verdict: int(count) for verdict, count in rows}

    async def incorrect_item_ids(self, session_id: int) -> tuple[int, ...]:
        rows = await self._session.execute(
            select(ExerciseAttempt.content_item_id)
            .where(
                ExerciseAttempt.session_id == session_id,
                ExerciseAttempt.verdict != Verdict.CORRECT,
            )
            .distinct()
            .order_by(ExerciseAttempt.content_item_id)
        )
        return tuple(int(item_id) for item_id in rows.scalars())


class ProgressRepository(BaseRepository[UserProgress]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UserProgress)

    async def get_or_create(self, user_id: int, topic_id: int) -> UserProgress:
        progress = await self._session.scalar(
            select(UserProgress).where(
                UserProgress.user_id == user_id,
                UserProgress.topic_id == topic_id,
            )
        )
        if progress is None:
            progress = UserProgress(
                user_id=user_id,
                topic_id=topic_id,
                mastery=0.0,
                streak=0,
            )
            self._session.add(progress)
            await self._session.flush()
        return progress
