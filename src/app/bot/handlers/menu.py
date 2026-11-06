"""Grouped main menu.

Telegram's command list is flat, so the real hierarchy lives in inline
keyboards: ``/menu`` opens sections, and each section exposes the same
handlers as the slash commands.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import MenuCB
from app.bot.handlers.common import handle_new
from app.bot.handlers.correction import handle_correction
from app.bot.handlers.drill import handle_drill
from app.bot.handlers.preferences import handle_quiet, handle_timezone
from app.bot.handlers.privacy import handle_about_me, handle_delete_me
from app.bot.handlers.review import handle_review
from app.bot.handlers.roleplay import handle_roleplay, handle_stop
from app.bot.handlers.stats import handle_stats
from app.bot.handlers.vocab import handle_vocab
from app.bot.handlers.words import handle_words
from app.bot.keyboards.menu import (
    help_keyboard,
    learn_keyboard,
    main_menu_keyboard,
    practice_keyboard,
    progress_keyboard,
    settings_keyboard,
)
from app.core.di import AppServices
from app.texts import ru

_SECTIONS = {
    "root": (ru.MENU_TITLE, main_menu_keyboard),
    "learn": (ru.MENU_LEARN_TEXT, learn_keyboard),
    "practice": (ru.MENU_PRACTICE_TEXT, practice_keyboard),
    "progress": (ru.MENU_PROGRESS_TEXT, progress_keyboard),
    "settings": (ru.MENU_SETTINGS_TEXT, settings_keyboard),
    "help": (ru.HELP, help_keyboard),
}


async def handle_menu(message: Message) -> None:
    """``/menu``: show the top-level sections."""
    await message.answer(ru.MENU_TITLE, reply_markup=main_menu_keyboard())


async def handle_menu_callback(
    callback: CallbackQuery,
    callback_data: MenuCB,
    state: FSMContext,
    services: AppServices,
) -> None:
    """Navigate sections or launch the selected action."""
    target = callback_data.target
    section = _SECTIONS.get(target)
    if section is not None:
        text, keyboard = section
        if isinstance(callback.message, Message):
            await callback.message.edit_text(text, reply_markup=keyboard())
        await callback.answer()
        return

    message = _action_message(callback)
    if message is None:
        await callback.answer()
        return

    await callback.answer()
    await _run_action(target, message, state, services)


def _action_message(callback: CallbackQuery) -> Message | None:
    """Reuse the menu message as a user message for an existing handler.

    Callback messages belong to the bot, so ``from_user`` and ``text`` are
    replaced. Clearing the text prevents argument parsers from treating menu
    copy as a command argument.
    """
    if callback.from_user is None or not isinstance(callback.message, Message):
        return None
    return callback.message.model_copy(
        update={
            "from_user": callback.from_user,
            "text": None,
            "caption": None,
            "entities": None,
            "caption_entities": None,
        }
    )


async def _run_action(
    target: str,
    message: Message,
    state: FSMContext,
    services: AppServices,
) -> None:
    """Dispatch a menu action to the handler that owns it."""
    if target == "drill":
        await handle_drill(message, state, services)
    elif target == "words":
        await handle_words(message, state, services)
    elif target == "review":
        await handle_review(message, services)
    elif target == "vocab":
        await handle_vocab(message, services)
    elif target == "correction":
        await handle_correction(message, services)
    elif target == "roleplay":
        await handle_roleplay(message, services)
    elif target == "stop":
        await handle_stop(message, services)
    elif target == "new":
        await handle_new(message, services)
    elif target == "stats":
        await handle_stats(message, services)
    elif target == "timezone":
        await handle_timezone(message, services)
    elif target == "quiet":
        await handle_quiet(message, services)
    elif target == "about":
        await handle_about_me(message, services)
    elif target == "delete":
        await handle_delete_me(message, services)


def build_router() -> Router:
    """Wire /menu and its inline callbacks."""
    router = Router(name="menu")
    router.message.register(handle_menu, Command("menu"))
    router.callback_query.register(handle_menu_callback, MenuCB.filter())
    return router
