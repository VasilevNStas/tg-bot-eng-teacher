"""Inline keyboards for the grouped main menu."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import MenuCB
from app.texts import ru


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Top-level sections of the menu."""
    builder = InlineKeyboardBuilder()
    builder.button(text=ru.MENU_LEARN, callback_data=MenuCB(target="learn"))
    builder.button(text=ru.MENU_PRACTICE, callback_data=MenuCB(target="practice"))
    builder.button(text=ru.MENU_PROGRESS, callback_data=MenuCB(target="progress"))
    builder.button(text=ru.MENU_SETTINGS, callback_data=MenuCB(target="settings"))
    builder.button(text=ru.MENU_HELP, callback_data=MenuCB(target="help"))
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def learn_keyboard() -> InlineKeyboardMarkup:
    """Study activities: verbs, word cards, review and saved words."""
    builder = InlineKeyboardBuilder()
    builder.button(text=ru.MENU_DRILL, callback_data=MenuCB(target="drill"))
    builder.button(text=ru.MENU_WORDS, callback_data=MenuCB(target="words"))
    builder.button(text=ru.MENU_REVIEW, callback_data=MenuCB(target="review"))
    builder.button(text=ru.MENU_VOCAB, callback_data=MenuCB(target="vocab"))
    builder.button(text=ru.MENU_BACK, callback_data=MenuCB(target="root"))
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def practice_keyboard() -> InlineKeyboardMarkup:
    """Free-form practice: corrections and role-play."""
    builder = InlineKeyboardBuilder()
    builder.button(text=ru.MENU_CORRECTION, callback_data=MenuCB(target="correction"))
    builder.button(text=ru.MENU_ROLEPLAY, callback_data=MenuCB(target="roleplay"))
    builder.button(text=ru.MENU_STOP, callback_data=MenuCB(target="stop"))
    builder.button(text=ru.MENU_BACK, callback_data=MenuCB(target="root"))
    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()


def progress_keyboard() -> InlineKeyboardMarkup:
    """Read-only progress view."""
    builder = InlineKeyboardBuilder()
    builder.button(text=ru.MENU_STATS, callback_data=MenuCB(target="stats"))
    builder.button(text=ru.MENU_BACK, callback_data=MenuCB(target="root"))
    builder.adjust(1)
    return builder.as_markup()


def settings_keyboard() -> InlineKeyboardMarkup:
    """Profile and privacy settings."""
    builder = InlineKeyboardBuilder()
    builder.button(text=ru.MENU_TIMEZONE, callback_data=MenuCB(target="timezone"))
    builder.button(text=ru.MENU_QUIET, callback_data=MenuCB(target="quiet"))
    builder.button(text=ru.MENU_ABOUT, callback_data=MenuCB(target="about"))
    builder.button(text=ru.MENU_DELETE, callback_data=MenuCB(target="delete"))
    builder.button(text=ru.MENU_BACK, callback_data=MenuCB(target="root"))
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


def help_keyboard() -> InlineKeyboardMarkup:
    """Single back button for the help section."""
    builder = InlineKeyboardBuilder()
    builder.button(text=ru.MENU_BACK, callback_data=MenuCB(target="root"))
    return builder.as_markup()
