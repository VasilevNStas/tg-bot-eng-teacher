"""Telegram command menu."""

from __future__ import annotations

from aiogram.types import BotCommand

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="menu", description="Меню"),
    BotCommand(command="stats", description="Прогресс"),
    BotCommand(command="help", description="Справка"),
]
