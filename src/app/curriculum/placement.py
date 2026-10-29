"""Short deterministic placement test (ADR-017)."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from app.curriculum.models import MultipleChoicePayload
from app.db.database import Database
from app.db.enums import CEFRLevel, ContentKind
from app.db.models import ContentItem
from app.db.repositories.content import ContentRepository


@dataclass(frozen=True, slots=True)
class PlacementQuestion:
    level: CEFRLevel
    prompt: str
    options: tuple[str, ...]
    correct_index: int


QUESTIONS: tuple[PlacementQuestion, ...] = (
    PlacementQuestion(
        level=CEFRLevel.A1,
        prompt="I ___ a student.",
        options=("am", "is", "are"),
        correct_index=0,
    ),
    PlacementQuestion(
        level=CEFRLevel.A2,
        prompt="She ___ to the cinema yesterday.",
        options=("go", "goes", "went"),
        correct_index=2,
    ),
    PlacementQuestion(
        level=CEFRLevel.B1,
        prompt="I ___ here since 2020.",
        options=("live", "lived", "have lived"),
        correct_index=2,
    ),
    PlacementQuestion(
        level=CEFRLevel.B2,
        prompt="If I ___ more time, I would travel.",
        options=("have", "had", "would have"),
        correct_index=1,
    ),
    PlacementQuestion(
        level=CEFRLevel.C1,
        prompt="The proposal was turned ___ by the committee.",
        options=("down", "off", "up"),
        correct_index=0,
    ),
    PlacementQuestion(
        level=CEFRLevel.C2,
        prompt="Had I known, I ___ differently.",
        options=("would act", "would have acted", "had acted"),
        correct_index=1,
    ),
)


_LEVEL_ORDER = {level: index for index, level in enumerate(CEFRLevel)}


def _from_content_item(item: ContentItem) -> PlacementQuestion | None:
    if item.kind != ContentKind.MULTIPLE_CHOICE:
        return None
    try:
        payload = MultipleChoicePayload.model_validate(item.payload)
    except ValidationError:
        return None
    return PlacementQuestion(
        level=item.level,
        prompt=payload.question,
        options=tuple(payload.options),
        correct_index=payload.correct_index,
    )


async def load_placement_questions(database: Database) -> tuple[PlacementQuestion, ...]:
    """Load placement MCQs from the content bank, falling back to built-ins.

    A valid bank set is exactly one active ``multiple_choice`` item per CEFR
    level, tagged ``purpose:placement``. Anything else means the bank is not
    ready for placement, so the deterministic built-in questions are used.
    """
    async with database.session() as session:
        items = await ContentRepository(session).list_active(
            kind=ContentKind.MULTIPLE_CHOICE,
            tag="purpose:placement",
        )

    questions: dict[CEFRLevel, PlacementQuestion] = {}
    for item in items:
        question = _from_content_item(item)
        if question is None or question.level in questions:
            return QUESTIONS
        questions[question.level] = question

    if set(questions) != set(CEFRLevel):
        return QUESTIONS
    return tuple(sorted(questions.values(), key=lambda q: _LEVEL_ORDER[q.level]))


def score_to_level(correct: int) -> CEFRLevel:
    """Map the number of correct answers to a CEFR level.

    The test has one question per level, so the score is a coarse screen,
    not a full exam.
    """
    if not 0 <= correct <= len(QUESTIONS):
        raise ValueError(f"correct must be between 0 and {len(QUESTIONS)}")
    if correct <= 1:
        return CEFRLevel.A1
    return {
        2: CEFRLevel.A2,
        3: CEFRLevel.B1,
        4: CEFRLevel.B2,
        5: CEFRLevel.C1,
    }.get(correct, CEFRLevel.C2)
