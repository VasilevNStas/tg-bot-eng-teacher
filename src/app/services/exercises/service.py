"""Drill sessions over deterministic content."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.curriculum.models import ContentItemSpec, IrregularVerbPayload
from app.curriculum.selector import ContentSelector, SelectionCriteria
from app.db.database import Database
from app.db.enums import ContentKind, ExerciseState, Verdict
from app.db.models import ContentItem, ExerciseSession, User
from app.db.repositories.content import ContentRepository
from app.db.repositories.exercises import ExerciseRepository, ProgressRepository
from app.grading.grader import GradeResult, IrregularForm, grade_item
from app.services.exercises.errors import DrillSessionError, DrillUnavailable

DEFAULT_TOPIC_SLUG = "irregular-verbs"


@dataclass(frozen=True, slots=True)
class DrillQuestion:
    content_item_id: int
    slug: str
    base: str
    base_ipa: str | None
    translation_ru: str
    past: tuple[str, ...]
    participle: tuple[str, ...]
    past_ipa: str | None
    participle_ipa: str | None
    form: IrregularForm

    @property
    def forms(self) -> tuple[str, ...]:
        return self.past if self.form == "past" else self.participle

    @property
    def ipa(self) -> str | None:
        return self.past_ipa if self.form == "past" else self.participle_ipa


@dataclass(frozen=True, slots=True)
class DrillSession:
    session_id: int
    topic_id: int
    questions: tuple[DrillQuestion, ...]


@dataclass(frozen=True, slots=True)
class DrillAttemptResult:
    result: GradeResult
    question: DrillQuestion
    mastery: float
    streak: int


@dataclass(frozen=True, slots=True)
class SessionStats:
    total: int = 0
    correct: int = 0
    partial: int = 0
    incorrect: int = 0

    @classmethod
    def from_counts(cls, counts: dict[Verdict, int]) -> SessionStats:
        total = sum(counts.values())
        correct = counts.get(Verdict.CORRECT, 0)
        partial = counts.get(Verdict.PARTIAL, 0)
        return cls(
            total=total,
            correct=correct,
            partial=partial,
            incorrect=counts.get(Verdict.INCORRECT, 0),
        )


class ExerciseService:
    def __init__(
        self,
        database: Database,
        *,
        session_size: int = 5,
        rng: random.Random | None = None,
        topic_slug: str = DEFAULT_TOPIC_SLUG,
    ) -> None:
        self._database = database
        self._session_size = max(1, session_size)
        self._rng = rng or random.Random()
        self._topic_slug = topic_slug

    async def start_session(self, user: User) -> DrillSession:
        async with self._database.session() as session:
            content_repo = ContentRepository(session)
            topic = await content_repo.get_topic_by_slug(self._topic_slug)
            if topic is None:
                raise DrillUnavailable(f"topic '{self._topic_slug}' is not imported")

            selector = ContentSelector(content_repo, rng=self._rng)
            selected = await selector.select(
                SelectionCriteria(
                    topic_slug=self._topic_slug,
                    kind=ContentKind.IRREGULAR_VERB,
                    level=user.level,
                    limit=self._session_size,
                )
            )
            if not selected:
                selected = await selector.select(
                    SelectionCriteria(
                        topic_slug=self._topic_slug,
                        kind=ContentKind.IRREGULAR_VERB,
                        limit=self._session_size,
                    )
                )
            if not selected:
                raise DrillUnavailable(f"topic '{self._topic_slug}' has no active items")

            exercise_repo = ExerciseRepository(session)
            now = datetime.now(UTC)
            await exercise_repo.abandon_active(user.id, at=now)
            exercise_session = await exercise_repo.add(
                user_id=user.id,
                topic_id=topic.id,
                state=ExerciseState.ACTIVE,
                started_at=now,
            )

            questions = tuple(
                self._to_question(item, self._form_for(index))
                for index, item in enumerate(selected)
            )

        return DrillSession(
            session_id=exercise_session.id,
            topic_id=topic.id,
            questions=questions,
        )

    async def get_question(
        self,
        user: User,
        session_id: int,
        content_item_id: int,
        form: IrregularForm,
    ) -> DrillQuestion:
        async with self._database.session() as session:
            await self._require_active(session, user, session_id)
            item = await ContentRepository(session).get_active(content_item_id)
            if item is None:
                raise DrillUnavailable(f"content item {content_item_id} is unavailable")
            return self._to_question(item, form)

    async def submit_answer(
        self,
        user: User,
        *,
        session_id: int,
        content_item_id: int,
        form: IrregularForm,
        answer: str,
        latency_ms: int | None = None,
    ) -> DrillAttemptResult:
        async with self._database.session() as session:
            exercise_session = await self._require_active(session, user, session_id)
            item = await ContentRepository(session).get_active(content_item_id)
            if item is None:
                raise DrillUnavailable(f"content item {content_item_id} is unavailable")

            question = self._to_question(item, form)
            result = grade_item(self._to_spec(item), answer, form=form)

            exercise_repo = ExerciseRepository(session)
            await exercise_repo.add_attempt(
                user_id=user.id,
                content_item_id=item.id,
                session_id=exercise_session.id,
                answer=answer,
                verdict=result.verdict,
                checker=result.checker,
                latency_ms=None if latency_ms is None else max(0, latency_ms),
            )

            if exercise_session.topic_id is None:
                raise DrillSessionError(f"session {session_id} has no topic")
            progress = await ProgressRepository(session).get_or_create(
                user.id,
                exercise_session.topic_id,
            )
            score = {
                Verdict.CORRECT: 1.0,
                Verdict.PARTIAL: 0.5,
                Verdict.INCORRECT: 0.0,
            }[result.verdict]
            progress.mastery = round(
                min(1.0, max(0.0, 0.8 * progress.mastery + 0.2 * score)),
                4,
            )
            progress.streak = progress.streak + 1 if result.verdict is Verdict.CORRECT else 0
            progress.last_seen_at = datetime.now(UTC)

        return DrillAttemptResult(
            result=result,
            question=question,
            mastery=progress.mastery,
            streak=progress.streak,
        )

    async def cancel_active(self, user: User) -> SessionStats | None:
        async with self._database.session() as session:
            exercise_repo = ExerciseRepository(session)
            exercise_session = await exercise_repo.get_active(user.id)
            if exercise_session is None:
                return None
            await exercise_repo.finish(
                exercise_session,
                ExerciseState.ABANDONED,
                datetime.now(UTC),
            )
            counts = await exercise_repo.counts(exercise_session.id)

        return SessionStats.from_counts(counts)

    async def finish_session(
        self,
        user: User,
        session_id: int,
        *,
        state: ExerciseState,
    ) -> SessionStats:
        async with self._database.session() as session:
            exercise_repo = ExerciseRepository(session)
            exercise_session = await exercise_repo.get_for_user(session_id, user.id)
            if exercise_session is None:
                raise DrillSessionError(f"session {session_id} not found")
            await exercise_repo.finish(
                exercise_session,
                state,
                datetime.now(UTC),
            )
            counts = await exercise_repo.counts(session_id)

        return SessionStats.from_counts(counts)

    @staticmethod
    def _form_for(index: int) -> IrregularForm:
        return "past" if index % 2 == 0 else "participle"

    @staticmethod
    def _to_question(item: ContentItem, form: IrregularForm) -> DrillQuestion:
        spec = ExerciseService._to_spec(item)
        payload = spec.payload
        if not isinstance(payload, IrregularVerbPayload):
            raise DrillUnavailable(f"content item {item.slug} is not an irregular verb")
        if form not in ("past", "participle"):
            raise DrillSessionError(f"unknown irregular verb form: {form}")
        return DrillQuestion(
            content_item_id=item.id,
            slug=item.slug,
            base=payload.base,
            base_ipa=payload.base_ipa,
            translation_ru=payload.translation_ru,
            past=tuple(payload.past),
            participle=tuple(payload.participle),
            past_ipa=payload.past_ipa,
            participle_ipa=payload.participle_ipa,
            form=form,
        )

    @staticmethod
    def _to_spec(item: ContentItem) -> ContentItemSpec:
        return ContentItemSpec.model_validate(
            {
                "slug": item.slug,
                "level": item.level,
                "payload": item.payload,
                "source": item.source,
                "license": item.license,
                "active": item.is_active,
            }
        )

    @staticmethod
    async def _require_active(
        session: AsyncSession,
        user: User,
        session_id: int,
    ) -> ExerciseSession:
        exercise_session = await ExerciseRepository(session).get_for_user(
            session_id,
            user.id,
        )
        if exercise_session is None:
            raise DrillSessionError(f"session {session_id} not found")
        if exercise_session.state is not ExerciseState.ACTIVE:
            raise DrillSessionError(f"session {session_id} is not active")
        return exercise_session
