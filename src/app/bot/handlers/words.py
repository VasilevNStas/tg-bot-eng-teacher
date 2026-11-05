"""Vocabulary word cards: study, quiz, result.

Flow:

1. ``/words`` builds a card and shows a study list of the selected words.
2. "Quiz" switches to question mode; each text answer goes to
   ``WordCardService.submit_answer``.
3. The final answer finishes the session and shows the result keyboard with
   an optional "retry mistakes" button.

The handler keeps the question snapshot in FSM data so a restart of the bot
process does not re-randomize the card; the real progress lives in the DB.
"""

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
from app.bot.keyboards.task import task_keyboard
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
    """Return the ``words`` section of the JSON profile, or an empty dict."""
    profile = user.profile if isinstance(user.profile, dict) else {}
    section = profile.get(PROFILE_SECTION)
    return section if isinstance(section, dict) else {}


def _card_size(user: User) -> int:
    """Read the saved card size, falling back to the default on bad data."""
    value = _section(user).get("card_size")
    if isinstance(value, int) and 1 <= value <= 20:
        return value
    return DEFAULT_CARD_SIZE


def _direction(user: User) -> WordCardDirection:
    """Read the saved direction; unknown values fall back to MIX."""
    value = _section(user).get("direction")
    if isinstance(value, str):
        try:
            return WordCardDirection(value)
        except ValueError:
            pass
    return WordCardDirection.MIX


async def _save_settings(services: AppServices, user: User, **values: object) -> None:
    """Merge settings into ``profile.words`` without touching other sections."""
    async with services.database.session() as session:
        await UserRepository(session).update_profile_section(
            user.tg_id,
            PROFILE_SECTION,
            values,
        )


def _questions_data(card: WordCardSession) -> list[dict[str, Any]]:
    """Serialize questions into FSM-friendly dicts.

    FSM storage is JSON-based, so dataclasses are converted to plain dicts.
    Directions are stored as strings for the same reason.
    """
    return [
        {
            "id": question.content_item_id,
            "direction": question.direction.value,
            "prompt": question.prompt,
            "term": question.term,
            "translations": [question.translation_ru],
            "transcription": question.transcription,
        }
        for question in card.questions
    ]


def _format_study(questions: list[dict[str, Any]]) -> str:
    """Render the study list, one line per word.

    With ``direction=both`` each item produces two questions (en→ru and
    ru→en); the study list must still show the word only once.
    """
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
    """Render one quiz prompt with its direction hint."""
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
    """Render grading feedback; show the expected answer unless correct."""
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
    """Render current card size and direction."""
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
    """Start a card and move the FSM to the study state.

    ``edit`` is set when the command came from a callback: the existing
    message is edited instead of sending a new one. Returns ``False`` when
    the bank is empty so callers can stop early.
    """
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
    """Ask one question and remember when it was shown for latency stats."""
    await state.update_data(started_at=time.time())
    await message.answer(
        _format_question(questions[index], index + 1, len(questions)),
        reply_markup=task_keyboard(),
    )


async def handle_words(
    message: Message,
    state: FSMContext,
    services: AppServices,
) -> None:
    """``/words``: start a fresh card with the user's saved settings."""
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
    """Switch from the study list to the first quiz question."""
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
    """Show the settings screen."""
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
    """Save the selected card size and immediately start a matching card."""
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
    # Re-read the user so the new setting is visible to ``_start_card``.
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
    """Save the selected direction and immediately start a matching card."""
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
    """Start a card containing only the words answered incorrectly."""
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
    """Start a brand-new card with the saved settings."""
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
    """Grade a text answer, advance to the next question or finish the card.

    FSM data is treated as untrusted: it may be stale after a deploy, so every
    field is type-checked before use and the session is cleared on mismatch.
    """
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

    # Last question answered: close the session and show the summary.
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
    """Register word-card commands and callbacks on a dedicated router."""
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
