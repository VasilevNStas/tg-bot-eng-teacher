"""Vocabulary word cards: study, quiz, result."""

from __future__ import annotations

import html
import time
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import WordsCB
from app.bot.keyboards.onboarding import start_keyboard
from app.bot.keyboards.words import CARD_SIZES, result_keyboard, settings_keyboard, study_keyboard
from app.bot.states import Words
from app.bot.user_context import resolve_user
from app.core.di import AppServices
from app.db.enums import Verdict, WordDirection
from app.db.models import User
from app.db.repositories.users import UserRepository
from app.services.words.errors import WordCardError, WordCardSessionError, WordCardUnavailable
from app.services.words.service import (
    WordAttemptResult,
    WordCardDirection,
    WordCardSession,
)
from app.texts import ru

DEFAULT_CARD_SIZE = 10
PROFILE_SECTION = "words"


def _section(user: User) -> dict[str, Any]:
    profile = user.profile if isinstance(user.profile, dict) else {}
    section = profile.get(PROFILE_SECTION)
    return section if isinstance(section, dict) else {}


def _card_size(user: User) -> int:
    value = _section(user).get("card_size")
    if isinstance(value, int) and 1 <= value <= 20:
        return value
    return DEFAULT_CARD_SIZE


def _direction(user: User) -> WordCardDirection:
    value = _section(user).get("direction")
    if isinstance(value, str):
        try:
            return WordCardDirection(value)
        except ValueError:
            pass
    return WordCardDirection.MIX


async def _save_settings(services: AppServices, user: User, **values: object) -> None:
    async with services.database.session() as session:
        await UserRepository(session).update_profile_section(
            user.tg_id,
            PROFILE_SECTION,
            values,
        )


def _questions_data(card: WordCardSession) -> list[dict[str, Any]]:
    return [
        {
            "id": question.content_item_id,
            "direction": question.direction.value,
            "prompt": question.prompt,
            "term": question.term,
            "translations": list(question.translations_ru),
            "transcription": question.transcription,
        }
        for question in card.questions
    ]


def _format_study(questions: list[dict[str, Any]]) -> str:
    unique: dict[int, dict[str, Any]] = {}
    for question in questions:
        unique.setdefault(int(question["id"]), question)

    lines = [ru.WORDS_HEADER.format(count=len(unique)), ""]
    for number, question in enumerate(unique.values(), start=1):
        transcription = question.get("transcription")
        ipa = f" [{html.escape(str(transcription))}]" if transcription else ""
        lines.append(
            ru.WORDS_STUDY_ITEM.format(
                number=number,
                term=html.escape(str(question["term"])),
                ipa=ipa,
                translation=html.escape(", ".join(question["translations"])),
            )
        )
    lines.extend(["", ru.WORDS_STUDY_FOOTER])
    return "\n".join(lines)


def _format_question(question: dict[str, Any], number: int, total: int) -> str:
    direction = (
        ru.WORDS_DIRECTION_EN_RU
        if question["direction"] == WordDirection.EN_RU.value
        else ru.WORDS_DIRECTION_RU_EN
    )
    return ru.WORDS_QUESTION.format(
        number=number,
        total=total,
        prompt=html.escape(str(question["prompt"])),
        direction=direction,
    )


def _format_attempt(attempt: WordAttemptResult) -> str:
    result = attempt.result
    if result.verdict is Verdict.CORRECT:
        header = ru.WORDS_CORRECT
    elif result.verdict is Verdict.PARTIAL:
        header = ru.WORDS_PARTIAL
    else:
        header = ru.WORDS_INCORRECT

    parts = [header]
    if result.verdict is not Verdict.CORRECT:
        parts.append(ru.WORDS_EXPECTED.format(expected=html.escape(result.expected)))
    if result.normalized_answer:
        parts.append(ru.WORDS_ANSWER.format(answer=html.escape(result.normalized_answer)))
    return "\n".join(parts)


def _format_settings(user: User) -> str:
    return ru.WORDS_SETTINGS.format(
        size=_card_size(user),
        direction=ru.WORDS_DIRECTION_LABELS[_direction(user).value],
    )


async def _start_card(
    message: Message,
    state: FSMContext,
    services: AppServices,
    user: User,
    *,
    size: int | None = None,
    direction: WordCardDirection | None = None,
    item_ids: list[int] | None = None,
    edit: Message | None = None,
) -> bool:
    try:
        card = await services.words.start_card(
            user,
            direction=direction or _direction(user),
            card_size=size or _card_size(user),
            item_ids=item_ids,
        )
    except (WordCardUnavailable, WordCardError):
        await message.answer(ru.WORDS_UNAVAILABLE)
        return False

    questions = _questions_data(card)
    await state.set_state(Words.study)
    await state.update_data(
        session_id=card.session_id,
        questions=questions,
        index=0,
        wrong_item_ids=[],
    )
    text = _format_study(questions)
    if edit is not None:
        await edit.edit_text(text, reply_markup=study_keyboard())
    else:
        await message.answer(text, reply_markup=study_keyboard())
    return True


async def _send_question(
    message: Message,
    state: FSMContext,
    questions: list[dict[str, Any]],
    index: int,
) -> None:
    await state.update_data(started_at=time.time())
    await message.answer(_format_question(questions[index], index + 1, len(questions)))


async def handle_words(
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

    await state.clear()
    await _start_card(message, state, services, user)


async def handle_quiz(
    callback: CallbackQuery,
    state: FSMContext,
    services: AppServices,
) -> None:
    if callback.from_user is None:
        return

    data = await state.get_data()
    questions = data.get("questions")
    session_id = data.get("session_id")
    if not isinstance(questions, list) or not questions or not isinstance(session_id, int):
        await callback.answer(ru.WORDS_SESSION_ERROR)
        return

    await state.set_state(Words.quiz)
    await state.update_data(index=0)
    await callback.answer()
    if isinstance(callback.message, Message):
        await _send_question(callback.message, state, questions, 0)


async def handle_settings(
    callback: CallbackQuery,
    services: AppServices,
) -> None:
    if callback.from_user is None:
        return

    user = await resolve_user(services, callback.from_user)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _format_settings(user),
            reply_markup=settings_keyboard(_card_size(user), _direction(user)),
        )
    await callback.answer()


async def handle_size(
    callback: CallbackQuery,
    callback_data: WordsCB,
    state: FSMContext,
    services: AppServices,
) -> None:
    if callback.from_user is None:
        return

    try:
        size = int(callback_data.value)
    except ValueError:
        await callback.answer(ru.WORDS_SESSION_ERROR)
        return
    if size not in CARD_SIZES:
        await callback.answer(ru.WORDS_SESSION_ERROR)
        return
    if not isinstance(callback.message, Message):
        await callback.answer(ru.WORDS_SESSION_ERROR)
        return

    user = await resolve_user(services, callback.from_user)
    await _save_settings(services, user, card_size=size)
    user = await resolve_user(services, callback.from_user)
    await callback.answer()
    await _start_card(
        callback.message,
        state,
        services,
        user,
        size=size,
        edit=callback.message,
    )


async def handle_direction(
    callback: CallbackQuery,
    callback_data: WordsCB,
    state: FSMContext,
    services: AppServices,
) -> None:
    if callback.from_user is None:
        return

    try:
        direction = WordCardDirection(callback_data.value)
    except ValueError:
        await callback.answer(ru.WORDS_SESSION_ERROR)
        return
    if not isinstance(callback.message, Message):
        await callback.answer(ru.WORDS_SESSION_ERROR)
        return

    user = await resolve_user(services, callback.from_user)
    await _save_settings(services, user, direction=direction.value)
    user = await resolve_user(services, callback.from_user)
    await callback.answer()
    await _start_card(
        callback.message,
        state,
        services,
        user,
        direction=direction,
        edit=callback.message,
    )


async def handle_retry(
    callback: CallbackQuery,
    state: FSMContext,
    services: AppServices,
) -> None:
    if callback.from_user is None:
        return

    data = await state.get_data()
    wrong = data.get("wrong_item_ids")
    if not isinstance(wrong, list) or not wrong:
        await callback.answer(ru.WORDS_NO_ERRORS_TO_RETRY)
        return
    if not isinstance(callback.message, Message):
        await callback.answer(ru.WORDS_SESSION_ERROR)
        return

    user = await resolve_user(services, callback.from_user)
    item_ids = [int(item_id) for item_id in wrong]
    await callback.answer()
    await _start_card(
        callback.message,
        state,
        services,
        user,
        size=len(item_ids),
        item_ids=item_ids,
        edit=callback.message,
    )


async def handle_new(
    callback: CallbackQuery,
    state: FSMContext,
    services: AppServices,
) -> None:
    if callback.from_user is None:
        return
    if not isinstance(callback.message, Message):
        await callback.answer(ru.WORDS_SESSION_ERROR)
        return

    user = await resolve_user(services, callback.from_user)
    await callback.answer()
    await _start_card(callback.message, state, services, user, edit=callback.message)


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
        await message.answer(ru.WORDS_SESSION_ERROR)
        return

    question = questions[index]
    if not isinstance(question, dict):
        await state.clear()
        await message.answer(ru.WORDS_SESSION_ERROR)
        return

    user = await resolve_user(services, message.from_user)
    started_at = data.get("started_at")
    latency_ms = None
    if isinstance(started_at, int | float):
        latency_ms = int(max(0.0, time.time() - started_at) * 1000)

    try:
        attempt = await services.words.submit_answer(
            user,
            session_id=session_id,
            content_item_id=int(question["id"]),
            direction=WordDirection(question["direction"]),
            answer=message.text,
            latency_ms=latency_ms,
        )
    except (WordCardSessionError, WordCardUnavailable):
        await state.clear()
        await message.answer(ru.WORDS_SESSION_ERROR)
        return

    await message.answer(_format_attempt(attempt))

    next_index = index + 1
    if next_index < len(questions):
        await state.update_data(index=next_index)
        await _send_question(message, state, questions, next_index)
        return

    try:
        stats = await services.words.finish_card(user, session_id)
    except WordCardSessionError:
        await state.clear()
        await message.answer(ru.WORDS_SESSION_ERROR)
        return

    await state.set_state(Words.study)
    await state.update_data(index=0, wrong_item_ids=list(stats.wrong_item_ids))
    await message.answer(
        ru.WORDS_SUMMARY.format(
            correct=stats.correct,
            total=stats.total,
            partial=stats.partial,
            incorrect=stats.incorrect,
        ),
        reply_markup=result_keyboard(has_wrong=bool(stats.wrong_item_ids)),
    )


def build_router() -> Router:
    router = Router(name="words")
    router.message.register(handle_words, Command("words"))
    router.callback_query.register(handle_quiz, WordsCB.filter(F.action == "quiz"))
    router.callback_query.register(handle_settings, WordsCB.filter(F.action == "settings"))
    router.callback_query.register(handle_size, WordsCB.filter(F.action == "size"))
    router.callback_query.register(handle_direction, WordsCB.filter(F.action == "direction"))
    router.callback_query.register(handle_retry, WordsCB.filter(F.action == "retry"))
    router.callback_query.register(handle_new, WordsCB.filter(F.action == "new"))
    router.message.register(
        handle_answer,
        StateFilter(Words.quiz),
        F.text & ~F.text.startswith("/"),
    )
    return router


router = build_router()
