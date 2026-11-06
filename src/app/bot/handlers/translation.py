"""Russian-to-English translation practice (class B).

``/translate`` starts a short session from the translation bank. The first
pass is deterministic; the LLM judge is only consulted when the answer does
not match the reference. Every attempt is persisted like other exercises.
"""

from __future__ import annotations

import html
import time

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards.onboarding import start_keyboard
from app.bot.keyboards.task import task_keyboard
from app.bot.states import Translation
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.db.enums import ExerciseState, Verdict
from app.db.models import User
from app.services.exercises.service import SessionStats
from app.services.translation.errors import TranslationSessionError, TranslationUnavailable
from app.services.translation.service import TranslationAttemptResult, TranslationQuestion
from app.texts import ru


async def handle_translate(
    message: Message,
    state: FSMContext,
    services: AppServices,
) -> None:
    """``/translate``: start a session and ask the first sentence."""
    if message.from_user is None:
        return

    user = await resolve_user(services, message.from_user)
    if user.onboarding_state != "done":
        await message.answer(ru.ONBOARDING_REQUIRED, reply_markup=start_keyboard())
        return

    try:
        session = await services.translation.start_session(user)
    except TranslationUnavailable:
        await message.answer(ru.TRANSLATION_UNAVAILABLE)
        return

    item_ids = [question.content_item_id for question in session.questions]
    if not item_ids:
        await message.answer(ru.TRANSLATION_UNAVAILABLE)
        return

    await state.set_state(Translation.active)
    await state.update_data(
        session_id=session.session_id,
        item_ids=item_ids,
        index=0,
    )
    await _send_question(
        message,
        state,
        services,
        user,
        session.session_id,
        item_ids[0],
        number=1,
        total=len(item_ids),
    )


async def handle_answer(
    message: Message,
    state: FSMContext,
    services: AppServices,
) -> None:
    """Grade one translation, ask the next one or finish the session."""
    if message.text is None or message.from_user is None:
        return
    if len(message.text) > services.settings.max_user_message_chars:
        await message.answer(ru.MESSAGE_TOO_LONG)
        return

    data = await state.get_data()
    session_id = data.get("session_id")
    item_ids = data.get("item_ids")
    index = data.get("index")
    if (
        not isinstance(session_id, int)
        or not isinstance(item_ids, list)
        or not all(isinstance(item, int) for item in item_ids)
        or not isinstance(index, int)
        or index >= len(item_ids)
    ):
        await state.clear()
        await message.answer(ru.TRANSLATION_SESSION_ERROR)
        return

    user = await resolve_user(services, message.from_user)
    started_at = data.get("started_at")
    latency_ms = None
    if isinstance(started_at, int | float):
        latency_ms = int(max(0.0, time.time() - started_at) * 1000)

    try:
        attempt = await services.translation.submit_answer(
            user,
            session_id=session_id,
            content_item_id=item_ids[index],
            answer=message.text,
            latency_ms=latency_ms,
        )
    except (TranslationSessionError, TranslationUnavailable):
        await state.clear()
        await message.answer(ru.TRANSLATION_SESSION_ERROR)
        return

    await message.answer(_format_attempt(attempt))

    next_index = index + 1
    if next_index < len(item_ids):
        await state.update_data(index=next_index)
        await _send_question(
            message,
            state,
            services,
            user,
            session_id,
            item_ids[next_index],
            number=next_index + 1,
            total=len(item_ids),
        )
        return

    try:
        stats = await services.translation.finish_session(
            user,
            session_id,
            state=ExerciseState.FINISHED,
        )
    except TranslationSessionError:
        await state.clear()
        await message.answer(ru.TRANSLATION_SESSION_ERROR)
        return

    await state.clear()
    await message.answer(_format_summary(stats))


async def _send_question(
    message: Message,
    state: FSMContext,
    services: AppServices,
    user: User,
    session_id: int,
    content_item_id: int,
    *,
    number: int,
    total: int,
) -> None:
    """Fetch the current prompt and ask it with the exit button."""
    try:
        question = await services.translation.get_question(
            user,
            session_id,
            content_item_id,
        )
    except (TranslationSessionError, TranslationUnavailable):
        await state.clear()
        await message.answer(ru.TRANSLATION_SESSION_ERROR)
        return

    await state.update_data(started_at=time.time())
    await message.answer(
        _format_question(question, number, total),
        reply_markup=task_keyboard(),
    )


def _format_question(question: TranslationQuestion, number: int, total: int) -> str:
    return ru.TRANSLATION_QUESTION.format(
        number=number,
        total=total,
        prompt=html.escape(question.prompt_ru),
    )


def _format_attempt(attempt: TranslationAttemptResult) -> str:
    result = attempt.result
    question = attempt.question

    if result.verdict is Verdict.CORRECT:
        header = ru.TRANSLATION_CORRECT
    elif result.verdict is Verdict.PARTIAL:
        header = ru.TRANSLATION_PARTIAL
    else:
        header = ru.TRANSLATION_INCORRECT

    parts = [header]
    if result.explanation:
        parts.append(ru.TRANSLATION_EXPLANATION.format(explanation=html.escape(result.explanation)))
    if result.normalized_answer:
        parts.append(ru.TRANSLATION_ANSWER.format(answer=html.escape(result.normalized_answer)))
    parts.append(ru.TRANSLATION_REFERENCE.format(reference=html.escape(question.reference)))
    return "\n".join(parts)


def _format_summary(stats: SessionStats) -> str:
    return ru.TRANSLATION_SUMMARY.format(
        correct=stats.correct,
        total=stats.total,
        partial=stats.partial,
    )


def build_router() -> Router:
    """Register /translate and its answer state."""
    router = Router(name="translation")
    router.message.register(handle_translate, Command("translate"))
    router.message.register(
        handle_answer,
        StateFilter(Translation.active),
        F.text & ~F.text.startswith("/"),
    )
    return router
