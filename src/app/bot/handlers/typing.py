"""Typing indicator helper shared by handlers that call the LLM."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender


@asynccontextmanager
async def typing_action(message: Message, *, enabled: bool) -> AsyncIterator[None]:
    """Send ``typing`` while a slow operation runs, unless disabled."""
    if not enabled or message.bot is None:
        yield
        return
    async with ChatActionSender.typing(
        bot=message.bot,
        chat_id=message.chat.id,
        initial_sleep=0.0,
    ):
        yield
