"""Telegram command menu."""

from __future__ import annotations

from aiogram.types import BotCommand

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="help", description="Справка"),
    BotCommand(command="correction", description="Разбор ошибок"),
    BotCommand(command="drill", description="Тренировка глаголов"),
    BotCommand(command="words", description="Карточки слов"),
    BotCommand(command="vocab", description="Сохранённые слова"),
    BotCommand(command="review", description="Повторение слов"),
    BotCommand(command="stats", description="Прогресс и слабые темы"),
    BotCommand(command="roleplay", description="Ролевая сцена"),
    BotCommand(command="stop", description="Завершить ролевую сцену"),
    BotCommand(command="timezone", description="Часовой пояс"),
    BotCommand(command="quiet", description="Тихие часы"),
    BotCommand(command="cancel", description="Завершить тренировку"),
    BotCommand(command="delete_me", description="Удалить мои данные"),
]
