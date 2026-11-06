"""Translation practice over the translation bank (class B).

The service owns the session lifecycle and delegates grading to the shared
deterministic ladder plus the optional LLM judge. The handler only stores ids
in FSM, so content edits are picked up between questions.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.curriculum.models import ContentItemSpec, TranslationPayload
from app.curriculum.selector import ContentSelector, SelectionCriteria
from app.db.database import Database
from app.db.enums import ContentKind, ExerciseSessionKind, ExerciseState
from app.db.models import ContentItem, ExerciseSession, User
from app.db.repositories.content import ContentRepository
from app.db.repositories.exercises import ExerciseRepository
from app.grading.grader import GradeResult
from app.grading.llm_judge import LLMJudge, grade_translation
from app.services.exercises.service import SessionStats
from app.services.translation.errors import TranslationSessionError, TranslationUnavailable

DEFAULT_TOPIC_SLUG = "translation-bank-b1"


@dataclass(frozen=True, slots=True)
class TranslationQuestion:
    """One Russian prompt and the reference shown after answering."""

    content_item_id: int
    slug: str
    prompt_ru: str
    reference: str
    notes_ru: str | None


@dataclass(frozen=True, slots=True)
class TranslationSession:
    """A started translation session with its ordered questions."""

    session_id: int
    topic_id: int
    questions: tuple[TranslationQuestion, ...]


@dataclass(frozen=True, slots=True)
class TranslationAttemptResult:
    """Grading outcome plus the question it belongs to."""

    result: GradeResult
    question: TranslationQuestion


class TranslationService:
    """Start, grade and finish translation practice sessions."""

    def __init__(
        self,
        database: Database,
        *,
        judge: LLMJudge | None = None,
        session_size: int = 3,
        rng: random.Random | None = None,
        topic_slug: str = DEFAULT_TOPIC_SLUG,
    ) -> None:
        self._database = database
        self._judge = judge
        self._session_size = max(1, session_size)
        self._rng = rng or random.Random()
        self._topic_slug = topic_slug

    async def start_session(self, user: User) -> TranslationSession:
        """Pick random translation pairs and open a clean session."""
        async with self._database.session() as session:
            content_repo = ContentRepository(session)
            topic = await content_repo.get_topic_by_slug(self._topic_slug)
            if topic is None:
                raise TranslationUnavailable(f"topic '{self._topic_slug}' is not imported")

            selector = ContentSelector(content_repo, rng=self._rng)
            selected = await selector.select(
                SelectionCriteria(
                    topic_slug=self._topic_slug,
                    kind=ContentKind.TRANSLATION_PAIR,
                    limit=self._session_size,
                )
            )
            if not selected:
                raise TranslationUnavailable(f"topic '{self._topic_slug}' has no active items")

            exercise_repo = ExerciseRepository(session)
            now = datetime.now(UTC)
            await exercise_repo.abandon_active(
                user.id,
                at=now,
                kind=ExerciseSessionKind.TRANSLATION,
            )
            exercise_session = await exercise_repo.add(
                user_id=user.id,
                topic_id=topic.id,
                kind=ExerciseSessionKind.TRANSLATION,
                state=ExerciseState.ACTIVE,
                started_at=now,
                meta={"item_ids": [item.id for item in selected]},
            )
            questions = tuple(self._to_question(item) for item in selected)

        return TranslationSession(
            session_id=exercise_session.id,
            topic_id=topic.id,
            questions=questions,
        )

    async def get_question(
        self,
        user: User,
        session_id: int,
        content_item_id: int,
    ) -> TranslationQuestion:
        """Re-render one question after a restart."""
        async with self._database.session() as session:
            exercise_session = await self._require_active(session, user, session_id)
            item = await ContentRepository(session).get_active(content_item_id)
            if item is None or item.topic_id != exercise_session.topic_id:
                raise TranslationUnavailable(f"content item {content_item_id} is unavailable")
            return self._to_question(item)

    async def submit_answer(
        self,
        user: User,
        *,
        session_id: int,
        content_item_id: int,
        answer: str,
        latency_ms: int | None = None,
    ) -> TranslationAttemptResult:
        """Grade one translation and persist the attempt."""
        async with self._database.session() as session:
            exercise_session = await self._require_active(session, user, session_id)
            item = await ContentRepository(session).get_active(content_item_id)
            if item is None or item.topic_id != exercise_session.topic_id:
                raise TranslationUnavailable(f"content item {content_item_id} is unavailable")

            question = self._to_question(item)
            result = await grade_translation(
                self._to_spec(item),
                answer,
                judge=self._judge,
                user_id=user.id,
            )
            await ExerciseRepository(session).add_attempt(
                user_id=user.id,
                content_item_id=item.id,
                session_id=exercise_session.id,
                answer=answer,
                verdict=result.verdict,
                checker=result.checker,
                latency_ms=None if latency_ms is None else max(0, latency_ms),
            )

        return TranslationAttemptResult(result=result, question=question)

    async def finish_session(
        self,
        user: User,
        session_id: int,
        *,
        state: ExerciseState,
    ) -> SessionStats:
        """Close a translation session and return its stats."""
        async with self._database.session() as session:
            exercise_repo = ExerciseRepository(session)
            exercise_session = await exercise_repo.get_for_user(session_id, user.id)
            if (
                exercise_session is None
                or exercise_session.kind is not ExerciseSessionKind.TRANSLATION
            ):
                raise TranslationSessionError(f"session {session_id} not found")
            await exercise_repo.finish(
                exercise_session,
                state,
                datetime.now(UTC),
            )
            counts = await exercise_repo.counts(session_id)

        return SessionStats.from_counts(counts)

    async def cancel_active(self, user: User) -> SessionStats | None:
        """Abandon the active translation session, if any."""
        async with self._database.session() as session:
            exercise_repo = ExerciseRepository(session)
            exercise_session = await exercise_repo.get_active(
                user.id,
                kind=ExerciseSessionKind.TRANSLATION,
            )
            if exercise_session is None:
                return None
            await exercise_repo.finish(
                exercise_session,
                ExerciseState.ABANDONED,
                datetime.now(UTC),
            )
            counts = await exercise_repo.counts(exercise_session.id)

        return SessionStats.from_counts(counts)

    @staticmethod
    async def _require_active(
        session: AsyncSession,
        user: User,
        session_id: int,
    ) -> ExerciseSession:
        """Load a translation session and verify ownership and state."""
        exercise_session = await ExerciseRepository(session).get_for_user(
            session_id,
            user.id,
        )
        if exercise_session is None or exercise_session.kind is not ExerciseSessionKind.TRANSLATION:
            raise TranslationSessionError(f"session {session_id} not found")
        if exercise_session.state is not ExerciseState.ACTIVE:
            raise TranslationSessionError(f"session {session_id} is not active")
        return exercise_session

    @staticmethod
    def _to_question(item: ContentItem) -> TranslationQuestion:
        """Convert a stored content row into a translation question."""
        spec = TranslationService._to_spec(item)
        payload = spec.payload
        if not isinstance(payload, TranslationPayload):
            raise TranslationUnavailable(f"content item {item.slug} is not a translation pair")
        return TranslationQuestion(
            content_item_id=item.id,
            slug=item.slug,
            prompt_ru=payload.prompt_ru,
            reference=payload.reference,
            notes_ru=payload.notes_ru,
        )

    @staticmethod
    def _to_spec(item: ContentItem) -> ContentItemSpec:
        """Rebuild a validated spec from a database row."""
        return ContentItemSpec.model_validate(
            {
                "slug": item.slug,
                "payload": {"kind": item.kind, **item.payload},
                "source": item.source,
                "license": item.license,
                "active": item.is_active,
            }
        )
