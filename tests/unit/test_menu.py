from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import MenuCB, TaskCB
from app.bot.keyboards.menu import (
    help_keyboard,
    learn_keyboard,
    main_menu_keyboard,
    practice_keyboard,
    progress_keyboard,
    settings_keyboard,
)
from app.bot.keyboards.task import task_keyboard
from app.texts import ru


def callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.callback_data or "" for row in markup.inline_keyboard for button in row]


def labels(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def test_main_menu_groups_sections() -> None:
    markup = main_menu_keyboard()

    assert labels(markup) == [
        ru.MENU_LEARN,
        ru.MENU_PRACTICE,
        ru.MENU_PROGRESS,
        ru.MENU_SETTINGS,
        ru.MENU_HELP,
    ]
    assert all("cancel" not in item for item in callbacks(markup))


def test_learn_section_exposes_study_actions() -> None:
    markup = learn_keyboard()

    assert labels(markup) == [
        ru.MENU_DRILL,
        ru.MENU_WORDS,
        ru.MENU_REVIEW,
        ru.MENU_VOCAB,
        ru.MENU_BACK,
    ]
    assert MenuCB(target="drill").pack() in callbacks(markup)


def test_practice_section_has_roleplay_and_stop() -> None:
    markup = practice_keyboard()

    assert labels(markup) == [
        ru.MENU_TRANSLATE,
        ru.MENU_CORRECTION,
        ru.MENU_ROLEPLAY,
        ru.MENU_STOP,
        ru.MENU_NEW,
        ru.MENU_BACK,
    ]
    assert MenuCB(target="translate").pack() in callbacks(markup)
    assert MenuCB(target="stop").pack() in callbacks(markup)
    assert MenuCB(target="new").pack() in callbacks(markup)


def test_progress_and_settings_sections() -> None:
    assert labels(progress_keyboard()) == [ru.MENU_STATS, ru.MENU_BACK]
    assert labels(settings_keyboard()) == [
        ru.MENU_GOALS,
        ru.MENU_TIMEZONE,
        ru.MENU_QUIET,
        ru.MENU_ABOUT,
        ru.MENU_DELETE,
        ru.MENU_BACK,
    ]


def test_help_and_task_keyboards() -> None:
    assert labels(help_keyboard()) == [ru.MENU_BACK]
    assert labels(task_keyboard()) == [ru.BUTTON_CANCEL_TASK]
    assert TaskCB(action="cancel").pack() in callbacks(task_keyboard())
