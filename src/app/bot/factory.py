"""Bot and dispatcher construction."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.middlewares.dedupe import UpdateDedupeMiddleware
from app.bot.middlewares.services import ServicesMiddleware
from app.bot.middlewares.user_lock import UserLockMiddleware, UserLockRegistry
from app.bot.routers import register_routers
from app.core.config import Settings
from app.core.di import AppServices


def create_bot(settings: Settings) -> Bot:
    """Create the aiogram bot with HTML parse mode for formatted replies."""
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(services: AppServices) -> Dispatcher:
    """Build the dispatcher and install update-level middlewares.

    Order matters: dedupe drops repeated Telegram updates, services injects the
    DI container, and the user lock serialises concurrent updates per user.
    """
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(UpdateDedupeMiddleware(services.database))
    dispatcher.update.outer_middleware(ServicesMiddleware(services))
    dispatcher.update.outer_middleware(
        UserLockMiddleware(
            UserLockRegistry(),
            timeout_seconds=services.settings.user_lock_timeout_seconds,
        )
    )
    register_routers(dispatcher)
    return dispatcher
