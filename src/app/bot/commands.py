"""Telegram command menu."""

from __future__ import annotations

from aiogram.types import BotCommand

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="help", description="Справка"),
    BotCommand(command="drill", description="Тренировка глаголов"),
    BotCommand(command="cancel", description="Завершить тренировку"),
]
