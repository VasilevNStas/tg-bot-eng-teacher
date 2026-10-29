"""Placement test and manual level selection."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot.callbacks import OnboardingCB
from app.bot.keyboards.onboarding import (
    levels_keyboard,
    question_keyboard,
    result_keyboard,
)
from app.bot.states import Onboarding
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.curriculum.placement import (
    QUESTIONS,
    PlacementQuestion,
    load_placement_questions,
    score_to_level,
)
from app.db.enums import CEFRLevel
from app.db.repositories.users import UserRepository
from app.texts import ru


async def _edit(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=reply_markup)


def _serialize_questions(
    questions: tuple[PlacementQuestion, ...],
) -> list[dict[str, object]]:
    return [
        {
            "level": question.level.value,
            "prompt": question.prompt,
            "options": list(question.options),
            "correct_index": question.correct_index,
        }
        for question in questions
    ]


def _deserialize_questions(raw: object) -> tuple[PlacementQuestion, ...]:
    if not isinstance(raw, list):
        return QUESTIONS
    try:
        questions = tuple(
            PlacementQuestion(
                level=CEFRLevel(str(item["level"])),
                prompt=str(item["prompt"]),
                options=tuple(str(option) for option in item["options"]),
                correct_index=int(item["correct_index"]),
            )
            for item in raw
        )
    except (KeyError, TypeError, ValueError):
        return QUESTIONS
    return questions or QUESTIONS


async def _show_question(
    callback: CallbackQuery,
    index: int,
    questions: tuple[PlacementQuestion, ...],
) -> None:
    question = questions[index]
    await _edit(
        callback,
        ru.PLACEMENT_QUESTION.format(
            number=index + 1,
            total=len(questions),
            prompt=question.prompt,
        ),
        reply_markup=question_keyboard(index, question.options),
    )


async def _save_level(services: AppServices, tg_id: int, level: CEFRLevel) -> None:
    async with services.database.session() as session:
        await UserRepository(session).update_level(tg_id, level)


async def handle_test(
    callback: CallbackQuery,
    state: FSMContext,
    services: AppServices,
) -> None:
    questions = await load_placement_questions(services.database)
    await state.set_state(Onboarding.testing)
    await state.update_data(
        index=0,
        correct=0,
        questions=_serialize_questions(questions),
    )
    await _show_question(callback, 0, questions)
    await callback.answer()


async def handle_answer(
    callback: CallbackQuery,
    callback_data: OnboardingCB,
    state: FSMContext,
    services: AppServices,
) -> None:
    if await state.get_state() != Onboarding.testing.state:
        await callback.answer(ru.STALE_QUESTION)
        return

    data = await state.get_data()
    questions = _deserialize_questions(data.get("questions"))
    index = int(data.get("index", 0))
    if callback_data.index != index or not 0 <= index < len(questions):
        await callback.answer(ru.STALE_QUESTION)
        return

    question = questions[index]
    correct = int(data.get("correct", 0))
    if callback_data.value == str(question.correct_index):
        correct += 1

    next_index = index + 1
    if next_index < len(questions):
        await state.update_data(index=next_index, correct=correct)
        await _show_question(callback, next_index, questions)
        await callback.answer()
        return

    level = score_to_level(correct)
    await resolve_user(services, callback.from_user)
    await _save_level(services, callback.from_user.id, level)
    await state.clear()
    await _edit(
        callback,
        ru.PLACEMENT_RESULT.format(level=level.value),
        reply_markup=result_keyboard(),
    )
    await callback.answer()


async def handle_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Onboarding.choosing_level)
    await _edit(callback, ru.CHOOSE_LEVEL, reply_markup=levels_keyboard())
    await callback.answer()


async def handle_level(
    callback: CallbackQuery,
    callback_data: OnboardingCB,
    state: FSMContext,
    services: AppServices,
) -> None:
    try:
        level = CEFRLevel(callback_data.value)
    except ValueError:
        await callback.answer(ru.UNKNOWN_LEVEL)
        return

    await resolve_user(services, callback.from_user)
    await _save_level(services, callback.from_user.id, level)
    await state.clear()
    await _edit(callback, ru.LEVEL_SET.format(level=level.value))
    await callback.answer()


def build_router() -> Router:
    router = Router(name="onboarding")
    router.callback_query.register(handle_test, OnboardingCB.filter(F.action == "test"))
    router.callback_query.register(
        handle_answer,
        OnboardingCB.filter(F.action == "answer"),
    )
    router.callback_query.register(handle_skip, OnboardingCB.filter(F.action == "skip"))
    router.callback_query.register(
        handle_level,
        OnboardingCB.filter(F.action == "level"),
    )
    return router
