"""Adaptive word cards over the vocabulary bank."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.curriculum.models import VocabularyPayload
from app.db.database import Database
from app.db.enums import (
    ContentKind,
    ExerciseSessionKind,
    ExerciseState,
    Verdict,
    WordDirection,
)
from app.db.models import ContentItem, ExerciseSession, User, WordProgress
from app.db.repositories.content import ContentRepository
from app.db.repositories.exercises import ExerciseRepository
from app.db.repositories.word_progress import WordProgressRepository
from app.grading.grader import GradeResult, grade_answer
from app.services.words.errors import (
    WordCardError,
    WordCardSessionError,
    WordCardUnavailable,
)

NEW_WORD_QUOTA = 0.4
RECENT_CARDS_EXCLUDED = 2
RECENCY_CAP_DAYS = 30.0


class WordCardDirection(StrEnum):
    EN_RU = "en_ru"
    RU_EN = "ru_en"
    BOTH = "both"
    MIX = "mix"


@dataclass(frozen=True, slots=True)
class WordQuestion:
    content_item_id: int
    term: str
    translations_ru: tuple[str, ...]
    accepted_en: tuple[str, ...]
    transcription: str | None
    example_en: str | None
    direction: WordDirection

    @property
    def prompt(self) -> str:
        if self.direction is WordDirection.EN_RU:
            return self.term
        return " / ".join(self.translations_ru)


@dataclass(frozen=True, slots=True)
class WordCardSession:
    session_id: int
    direction: WordCardDirection
    questions: tuple[WordQuestion, ...]
    item_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WordAttemptResult:
    result: GradeResult
    question: WordQuestion
    attempt_count: int
    correct_count: int
    streak: int


@dataclass(frozen=True, slots=True)
class WordCardStats:
    total: int
    correct: int
    partial: int
    incorrect: int
    wrong_item_ids: tuple[int, ...]

    @classmethod
    def from_counts(
        cls,
        counts: dict[Verdict, int],
        wrong_item_ids: tuple[int, ...],
    ) -> WordCardStats:
        return cls(
            total=sum(counts.values()),
            correct=counts.get(Verdict.CORRECT, 0),
            partial=counts.get(Verdict.PARTIAL, 0),
            incorrect=counts.get(Verdict.INCORRECT, 0),
            wrong_item_ids=wrong_item_ids,
        )


@dataclass(frozen=True, slots=True)
class _Candidate:
    item: ContentItem
    payload: VocabularyPayload
    progress: WordProgress | None
    frequency_norm: float

    @property
    def is_new(self) -> bool:
        return self.progress is None or self.progress.shown_count == 0


class WordCardService:
    def __init__(
        self,
        database: Database,
        *,
        card_size: int = 10,
        rng: random.Random | None = None,
    ) -> None:
        self._database = database
        self._card_size = max(1, card_size)
        self._rng = rng or random.Random()

    async def start_card(
        self,
        user: User,
        *,
        direction: WordCardDirection = WordCardDirection.MIX,
        card_size: int | None = None,
        source: str = "adaptive",
        now: datetime | None = None,
    ) -> WordCardSession:
        size = self._card_size if card_size is None else card_size
        if size < 1:
            raise WordCardError("card_size must be positive")
        if source not in {"adaptive", "random"}:
            raise WordCardError(f"unsupported card source: {source}")
        at = now or datetime.now(UTC)

        async with self._database.session() as session:
            candidates = await self._candidates(session, user, size=size)
            if not candidates:
                raise WordCardUnavailable("vocabulary bank is empty")

            selected = self._select(candidates, size=size, source=source, now=at)
            questions = self._build_questions(selected, direction)

            exercise_repo = ExerciseRepository(session)
            await exercise_repo.abandon_active(
                user.id,
                at=at,
                kind=ExerciseSessionKind.WORDS,
            )
            item_ids = tuple(dict.fromkeys(question.content_item_id for question in questions))
            exercise_session = await exercise_repo.add(
                user_id=user.id,
                topic_id=None,
                kind=ExerciseSessionKind.WORDS,
                meta={
                    "item_ids": list(item_ids),
                    "questions": [
                        {
                            "item_id": question.content_item_id,
                            "direction": question.direction.value,
                        }
                        for question in questions
                    ],
                    "card_size": size,
                    "source": source,
                    "direction": direction.value,
                },
                state=ExerciseState.ACTIVE,
                started_at=at,
            )

            progress_repo = WordProgressRepository(session)
            for candidate in selected:
                progress = await progress_repo.get_or_create(user.id, candidate.item.id)
                await progress_repo.mark_shown(progress, at)

            session_id = exercise_session.id

        return WordCardSession(
            session_id=session_id,
            direction=direction,
            questions=questions,
            item_ids=item_ids,
        )

    async def active_card(self, user: User) -> WordCardSession | None:
        async with self._database.session() as session:
            exercise_session = await ExerciseRepository(session).get_active(
                user.id,
                kind=ExerciseSessionKind.WORDS,
            )
            if exercise_session is None:
                return None
            return await self._restore(session, exercise_session)

    async def submit_answer(
        self,
        user: User,
        *,
        session_id: int,
        content_item_id: int,
        direction: WordDirection,
        answer: str,
        latency_ms: int | None = None,
        now: datetime | None = None,
    ) -> WordAttemptResult:
        at = now or datetime.now(UTC)

        async with self._database.session() as session:
            exercise_session = await self._require_active(session, user, session_id)
            item_ids = tuple(int(value) for value in exercise_session.meta.get("item_ids", []))
            if content_item_id not in item_ids:
                raise WordCardSessionError(
                    f"content item {content_item_id} is not part of session {session_id}"
                )

            item = await ContentRepository(session).get_active(content_item_id)
            if item is None:
                raise WordCardUnavailable(f"content item {content_item_id} is unavailable")
            payload = VocabularyPayload.model_validate(item.payload)
            result = self._grade(payload, direction, answer)

            exercise_repo = ExerciseRepository(session)
            await exercise_repo.add_attempt(
                user_id=user.id,
                content_item_id=item.id,
                session_id=exercise_session.id,
                answer=answer,
                verdict=result.verdict,
                checker=result.checker,
                latency_ms=None if latency_ms is None else max(0, latency_ms),
                direction=direction,
            )

            progress_repo = WordProgressRepository(session)
            progress = await progress_repo.get_or_create(user.id, item.id)
            await progress_repo.record_attempt(progress, result.verdict, at)
            attempt_count = progress.attempt_count
            correct_count = progress.correct_count
            streak = progress.streak

        return WordAttemptResult(
            result=result,
            question=self._question(item.id, payload, direction),
            attempt_count=attempt_count,
            correct_count=correct_count,
            streak=streak,
        )

    async def finish_card(
        self,
        user: User,
        session_id: int,
        *,
        state: ExerciseState = ExerciseState.FINISHED,
        now: datetime | None = None,
    ) -> WordCardStats:
        at = now or datetime.now(UTC)

        async with self._database.session() as session:
            exercise_repo = ExerciseRepository(session)
            exercise_session = await exercise_repo.get_for_user(session_id, user.id)
            if exercise_session is None:
                raise WordCardSessionError(f"session {session_id} not found")
            if exercise_session.kind is not ExerciseSessionKind.WORDS:
                raise WordCardSessionError(f"session {session_id} is not a word card")
            await exercise_repo.finish(exercise_session, state, at)
            counts = await exercise_repo.counts(session_id)
            wrong_item_ids = await exercise_repo.incorrect_item_ids(session_id)

        return WordCardStats.from_counts(counts, wrong_item_ids)

    async def cancel_active(
        self,
        user: User,
        *,
        now: datetime | None = None,
    ) -> WordCardStats | None:
        at = now or datetime.now(UTC)

        async with self._database.session() as session:
            exercise_repo = ExerciseRepository(session)
            exercise_session = await exercise_repo.get_active(
                user.id,
                kind=ExerciseSessionKind.WORDS,
            )
            if exercise_session is None:
                return None
            await exercise_repo.finish(exercise_session, ExerciseState.ABANDONED, at)
            counts = await exercise_repo.counts(exercise_session.id)
            wrong_item_ids = await exercise_repo.incorrect_item_ids(exercise_session.id)

        return WordCardStats.from_counts(counts, wrong_item_ids)

    async def _candidates(
        self,
        session: AsyncSession,
        user: User,
        *,
        size: int,
    ) -> list[_Candidate]:
        content_repo = ContentRepository(session)
        items = await content_repo.list_active(kind=ContentKind.VOCABULARY, level=user.level)
        if len(items) < size:
            items = await content_repo.list_active(kind=ContentKind.VOCABULARY)

        payloads = {item.id: VocabularyPayload.model_validate(item.payload) for item in items}
        items = [item for item in items if not payloads[item.id].function_word]

        progress_repo = WordProgressRepository(session)
        progress_by_item = await progress_repo.list_for_user(user.id)
        recent = set(
            await progress_repo.recent_item_ids(
                user.id,
                limit=size * RECENT_CARDS_EXCLUDED,
            )
        )
        fresh = [item for item in items if item.id not in recent]
        if len(fresh) >= size:
            items = fresh

        frequencies = [math.log1p(payloads[item.id].frequency or 0) for item in items]
        max_frequency = max(frequencies, default=0.0) or 1.0
        return [
            _Candidate(
                item=item,
                payload=payloads[item.id],
                progress=progress_by_item.get(item.id),
                frequency_norm=math.log1p(payloads[item.id].frequency or 0) / max_frequency,
            )
            for item in items
        ]

    def _select(
        self,
        candidates: list[_Candidate],
        *,
        size: int,
        source: str,
        now: datetime,
    ) -> list[_Candidate]:
        if source == "random":
            return self._rng.sample(candidates, k=min(size, len(candidates)))

        new = [candidate for candidate in candidates if candidate.is_new]
        known = [candidate for candidate in candidates if not candidate.is_new]
        new_quota = min(len(new), max(1, round(size * NEW_WORD_QUOTA)))

        picked = self._weighted_sample(new, new_quota, now)
        picked.extend(self._weighted_sample(known, size - len(picked), now))
        if len(picked) < size:
            remaining = [candidate for candidate in candidates if candidate not in picked]
            picked.extend(self._weighted_sample(remaining, size - len(picked), now))
        self._rng.shuffle(picked)
        return picked

    def _weighted_sample(
        self,
        candidates: list[_Candidate],
        count: int,
        now: datetime,
    ) -> list[_Candidate]:
        pool = [(candidate, self._weight(candidate, now)) for candidate in candidates]
        picked: list[_Candidate] = []
        for _ in range(min(count, len(pool))):
            total = sum(weight for _, weight in pool)
            if total <= 0:
                index = self._rng.randrange(len(pool))
            else:
                threshold = self._rng.random() * total
                cumulative = 0.0
                index = 0
                for position, (_, weight) in enumerate(pool):
                    cumulative += weight
                    if cumulative >= threshold:
                        index = position
                        break
            picked.append(pool.pop(index)[0])
        return picked

    def _weight(self, candidate: _Candidate, now: datetime) -> float:
        if candidate.is_new:
            return 1.0 + 0.5 * candidate.frequency_norm

        progress = candidate.progress
        if progress is None:
            return 1.0
        seen = progress.attempt_count
        error_rate = (progress.wrong_count + 1) / (seen + 2)
        mastery = progress.correct_count / seen if seen else 0.0
        last_seen = progress.last_attempted_at or progress.last_shown_at
        if last_seen is None:
            recency = 1.0
        else:
            last_seen_utc = (
                last_seen.replace(tzinfo=UTC)
                if last_seen.tzinfo is None
                else last_seen.astimezone(UTC)
            )
            days = max(0.0, (now - last_seen_utc).total_seconds() / 86400)
            recency = min(days, RECENCY_CAP_DAYS) / RECENCY_CAP_DAYS
        return (
            0.05
            + 0.40 * error_rate
            + 0.25 * recency
            + 0.15 * (1.0 - mastery)
            + 0.20 * candidate.frequency_norm
        )

    def _build_questions(
        self,
        selected: list[_Candidate],
        direction: WordCardDirection,
    ) -> tuple[WordQuestion, ...]:
        questions: list[WordQuestion] = []
        for candidate in selected:
            for item_direction in self._directions(direction):
                questions.append(
                    self._question(candidate.item.id, candidate.payload, item_direction)
                )
        return tuple(questions)

    def _directions(self, direction: WordCardDirection) -> tuple[WordDirection, ...]:
        if direction is WordCardDirection.BOTH:
            return (WordDirection.EN_RU, WordDirection.RU_EN)
        if direction is WordCardDirection.MIX:
            return (self._rng.choice((WordDirection.EN_RU, WordDirection.RU_EN)),)
        return (WordDirection(direction.value),)

    @staticmethod
    def _question(
        content_item_id: int,
        payload: VocabularyPayload,
        direction: WordDirection,
    ) -> WordQuestion:
        return WordQuestion(
            content_item_id=content_item_id,
            term=payload.term,
            translations_ru=tuple(payload.translations_ru),
            accepted_en=tuple(payload.accepted_en),
            transcription=payload.transcription,
            example_en=payload.example_en,
            direction=direction,
        )

    @staticmethod
    def _grade(
        payload: VocabularyPayload,
        direction: WordDirection,
        answer: str,
    ) -> GradeResult:
        if direction is WordDirection.EN_RU:
            return grade_answer(answer, payload.translations_ru, fuzzy=False)
        return grade_answer(answer, (payload.term, *payload.accepted_en), fuzzy=True)

    async def _restore(
        self,
        session: AsyncSession,
        exercise_session: ExerciseSession,
    ) -> WordCardSession | None:
        meta: dict[str, Any] = exercise_session.meta
        content_repo = ContentRepository(session)
        questions: list[WordQuestion] = []
        for entry in meta.get("questions", []):
            item_id = int(entry["item_id"])
            direction = WordDirection(entry["direction"])
            item = await content_repo.get_active(item_id)
            if item is None:
                continue
            payload = VocabularyPayload.model_validate(item.payload)
            questions.append(self._question(item_id, payload, direction))
        if not questions:
            return None

        return WordCardSession(
            session_id=exercise_session.id,
            direction=WordCardDirection(meta.get("direction", WordCardDirection.MIX.value)),
            questions=tuple(questions),
            item_ids=tuple(dict.fromkeys(question.content_item_id for question in questions)),
        )

    @staticmethod
    async def _require_active(
        session: AsyncSession,
        user: User,
        session_id: int,
    ) -> ExerciseSession:
        exercise_session = await ExerciseRepository(session).get_for_user(session_id, user.id)
        if exercise_session is None:
            raise WordCardSessionError(f"session {session_id} not found")
        if exercise_session.kind is not ExerciseSessionKind.WORDS:
            raise WordCardSessionError(f"session {session_id} is not a word card")
        if exercise_session.state is not ExerciseState.ACTIVE:
            raise WordCardSessionError(f"session {session_id} is not active")
        return exercise_session
