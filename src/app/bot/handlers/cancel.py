"""Early exit from the current task.

``/cancel`` and the inline "exit" button share one implementation: figure out
which FSM state is active, abandon the matching session in the database, clear
the state and return the summary text.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import TaskCB
from app.bot.states import Drill, Words
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.db.enums import ExerciseState
from app.db.models import User
from app.services.exercises.errors import DrillSessionError
from app.texts import ru


async def cancel_active_task(
    state: FSMContext,
    services: AppServices,
    user: User,
) -> str | None:
    """Abandon the active drill or word card and return a summary message."""
    current = await state.get_state()

    if current == Drill.active.state:
        return await _cancel_drill(state, services, user)

    if current in (Words.study.state, Words.quiz.state):
        return await _cancel_words(state, services, user)

    # FSM data can be lost after a deploy; still close a dangling word card.
    stats = await services.words.cancel_active(user)
    if stats is not None:
        return _words_summary(stats.total, stats.correct)
    return None


async def _cancel_drill(
    state: FSMContext,
    services: AppServices,
    user: User,
) -> str:
    """Finish the drill session stored in FSM, if the id is still valid."""
    data = await state.get_data()
    session_id = data.get("session_id")
    if not isinstance(session_id, int):
        await state.clear()
        return ru.DRILL_SESSION_ERROR

    try:
        stats = await services.exercise.finish_session(
            user,
            session_id,
            state=ExerciseState.ABANDONED,
        )
    except DrillSessionError:
        await state.clear()
        return ru.DRILL_SESSION_ERROR

    await state.clear()
    if stats.total == 0:
        return ru.DRILL_CANCELLED
    return ru.DRILL_CANCELLED_STATS.format(correct=stats.correct, total=stats.total)


async def _cancel_words(
    state: FSMContext,
    services: AppServices,
    user: User,
) -> str:
    """Abandon the active word card and clear its FSM state."""
    stats = await services.words.cancel_active(user)
    await state.clear()
    if stats is None:
        return ru.TASK_NOTHING_TO_CANCEL
    return _words_summary(stats.total, stats.correct)


def _words_summary(total: int, correct: int) -> str:
    """Pick the no-answers or with-stats cancellation message."""
    if total == 0:
        return ru.WORDS_CANCELLED
    return ru.WORDS_CANCELLED_STATS.format(correct=correct, total=total)


async def handle_cancel(
    message: Message,
    state: FSMContext,
    services: AppServices,
) -> None:
    """``/cancel``: leave the current task without finishing it."""
    if message.from_user is None:
        return

    user = await resolve_user(services, message.from_user)
    text = await cancel_active_task(state, services, user)
    await message.answer(text or ru.TASK_NOTHING_TO_CANCEL)


async def handle_task_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    services: AppServices,
) -> None:
    """Inline exit button: cancel the task and replace the question message."""
    if callback.from_user is None:
        return

    user = await resolve_user(services, callback.from_user)
    text = await cancel_active_task(state, services, user) or ru.TASK_NOTHING_TO_CANCEL
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text)
    await callback.answer()


def build_router() -> Router:
    """Wire /cancel and the inline task-exit callback."""
    router = Router(name="cancel")
    router.message.register(handle_cancel, Command("cancel"))
    router.callback_query.register(handle_task_cancel, TaskCB.filter())
    return router
