"""Irregular-verb drill sessions."""

from __future__ import annotations

import html
import time
from typing import Any, cast

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards.onboarding import start_keyboard
from app.bot.states import Drill
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.db.enums import ExerciseState, Verdict
from app.db.models import User
from app.grading.grader import IrregularForm
from app.services.exercises.errors import DrillSessionError, DrillUnavailable
from app.services.exercises.service import (
    DrillAttemptResult,
    DrillQuestion,
    SessionStats,
)
from app.texts import ru


async def handle_drill(
    message: Message,
    state: FSMContext,
    services: AppServices,
) -> None:
    if message.from_user is None:
        return

    user = await resolve_user(services, message.from_user)
    if user.onboarding_state != "done":
        await message.answer(ru.ONBOARDING_REQUIRED, reply_markup=start_keyboard())
        return

    try:
        session = await services.exercise.start_session(user)
    except DrillUnavailable:
        await message.answer(ru.DRILL_UNAVAILABLE)
        return

    questions = [
        {"id": question.content_item_id, "form": question.form} for question in session.questions
    ]
    await state.set_state(Drill.active)
    await state.update_data(
        session_id=session.session_id,
        questions=questions,
        index=0,
    )
    await _send_question(
        message,
        state,
        services,
        user,
        session.session_id,
        questions[0],
        1,
        len(questions),
    )


async def handle_cancel(
    message: Message,
    state: FSMContext,
    services: AppServices,
) -> None:
    if message.from_user is None:
        return

    data = await state.get_data()
    session_id = data.get("session_id")
    if not isinstance(session_id, int):
        await message.answer(ru.DRILL_NOTHING_TO_CANCEL)
        return

    user = await resolve_user(services, message.from_user)
    try:
        stats = await services.exercise.finish_session(
            user,
            session_id,
            state=ExerciseState.ABANDONED,
        )
    except DrillSessionError:
        await state.clear()
        await message.answer(ru.DRILL_SESSION_ERROR)
        return

    await state.clear()
    await message.answer(ru.DRILL_CANCELLED.format(correct=stats.correct, total=stats.total))


async def handle_answer(
    message: Message,
    state: FSMContext,
    services: AppServices,
) -> None:
    if message.text is None or message.from_user is None:
        return

    data = await state.get_data()
    session_id = data.get("session_id")
    questions = data.get("questions")
    index = data.get("index")

    if (
        not isinstance(session_id, int)
        or not isinstance(questions, list)
        or not isinstance(index, int)
        or index >= len(questions)
    ):
        await state.clear()
        await message.answer(ru.DRILL_SESSION_ERROR)
        return

    question = questions[index]
    if not isinstance(question, dict):
        await state.clear()
        await message.answer(ru.DRILL_SESSION_ERROR)
        return

    user = await resolve_user(services, message.from_user)
    started_at = data.get("started_at")
    latency_ms = None
    if isinstance(started_at, int | float):
        latency_ms = int(max(0.0, time.time() - started_at) * 1000)

    try:
        attempt = await services.exercise.submit_answer(
            user,
            session_id=session_id,
            content_item_id=int(question["id"]),
            form=cast(IrregularForm, question["form"]),
            answer=message.text,
            latency_ms=latency_ms,
        )
    except (DrillSessionError, DrillUnavailable):
        await state.clear()
        await message.answer(ru.DRILL_SESSION_ERROR)
        return

    await message.answer(_format_attempt(attempt))

    next_index = index + 1
    if next_index < len(questions):
        await state.update_data(index=next_index)
        await _send_question(
            message,
            state,
            services,
            user,
            session_id,
            questions[next_index],
            next_index + 1,
            len(questions),
        )
        return

    try:
        stats = await services.exercise.finish_session(
            user,
            session_id,
            state=ExerciseState.FINISHED,
        )
    except DrillSessionError:
        await state.clear()
        await message.answer(ru.DRILL_SESSION_ERROR)
        return

    await state.clear()
    await message.answer(_format_summary(stats))


async def _send_question(
    message: Message,
    state: FSMContext,
    services: AppServices,
    user: User,
    session_id: int,
    item: dict[str, Any],
    number: int,
    total: int,
) -> None:
    question = await services.exercise.get_question(
        user,
        session_id,
        int(item["id"]),
        cast(IrregularForm, item["form"]),
    )
    await state.update_data(started_at=time.time())
    await message.answer(_format_question(question, number, total))


def _format_question(question: DrillQuestion, number: int, total: int) -> str:
    form_label = ru.DRILL_FORM_PAST if question.form == "past" else ru.DRILL_FORM_PARTICIPLE
    return ru.DRILL_QUESTION.format(
        number=number,
        total=total,
        base=html.escape(question.base),
        ipa=_ipa_suffix(question.base_ipa),
        translation=html.escape(question.translation_ru),
        form=form_label,
    )


def _format_attempt(attempt: DrillAttemptResult) -> str:
    result = attempt.result
    question = attempt.question

    if result.verdict is Verdict.CORRECT:
        header = ru.DRILL_CORRECT
    elif result.verdict is Verdict.PARTIAL:
        header = ru.DRILL_PARTIAL
    else:
        header = ru.DRILL_INCORRECT

    parts = [header]
    if result.verdict is not Verdict.CORRECT:
        parts.append(ru.DRILL_EXPECTED.format(expected=html.escape(result.expected)))
    if result.normalized_answer:
        parts.append(ru.DRILL_ANSWER.format(answer=html.escape(result.normalized_answer)))

    details = ru.DRILL_DETAILS.format(
        base=html.escape(question.base),
        ipa=_ipa_suffix(question.base_ipa),
        translation=html.escape(question.translation_ru),
        past=_forms_display(question.past, question.past_ipa),
        participle=_forms_display(question.participle, question.participle_ipa),
    )
    return "\n".join(parts) + f"\n\n{details}"


def _format_summary(stats: SessionStats) -> str:
    return ru.DRILL_SUMMARY.format(
        correct=stats.correct,
        total=stats.total,
        partial=stats.partial,
    )


def _format_cancelled(stats: SessionStats) -> str:
    if stats.total == 0:
        return ru.DRILL_CANCELLED
    return ru.DRILL_CANCELLED_STATS.format(correct=stats.correct, total=stats.total)


def _ipa_suffix(ipa: str | None) -> str:
    if not ipa:
        return ""
    return f" [{html.escape(ipa)}]"


def _forms_display(forms: tuple[str, ...], ipa: str | None) -> str:
    value = " / ".join(html.escape(form) for form in forms)
    if ipa:
        value += f" [{html.escape(ipa)}]"
    return value


def build_router() -> Router:
    router = Router(name="drill")
    router.message.register(handle_drill, Command("drill"))
    router.message.register(handle_cancel, Command("cancel"))
    router.message.register(
        handle_answer,
        StateFilter(Drill.active),
        F.text & ~F.text.startswith("/"),
    )
    return router


router = build_router()
