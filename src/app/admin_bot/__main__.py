"""Entry point for the separate admin bot."""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from pydantic import ValidationError

from app.admin_bot.handlers import AdminContext, build_router
from app.admin_bot.middleware import AdminAccessMiddleware, AdminContextMiddleware
from app.bot.middlewares.rate_limit import RateLimitMiddleware
from app.core.admin_config import AdminSettings, get_admin_settings
from app.core.logging import get_logger, setup_logging
from app.db.database import Database
from app.services.admin.service import AdminService
from app.services.health import heartbeat_loop, write_heartbeat

logger = get_logger(__name__)

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="admin", description="Статус и счётчики"),
    BotCommand(command="health", description="Проверки здоровья"),
    BotCommand(command="users", description="Последние пользователи"),
    BotCommand(command="user", description="Профиль пользователя"),
    BotCommand(command="block", description="Заблокировать"),
    BotCommand(command="unblock", description="Разблокировать"),
    BotCommand(command="mute", description="Временное ограничение"),
    BotCommand(command="audit", description="Журнал действий"),
]


def create_bot(settings: AdminSettings) -> Bot:
    """Create the admin bot with HTML formatting for compact reports."""
    return Bot(
        token=settings.admin_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(context: AdminContext, settings: AdminSettings) -> Dispatcher:
    """Wire access control, flood control and the admin router."""
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(AdminAccessMiddleware(settings))
    if settings.rate_limit_enabled:
        dispatcher.update.outer_middleware(
            RateLimitMiddleware(limit=settings.rate_limit_commands_per_min)
        )
    dispatcher.update.outer_middleware(AdminContextMiddleware(context))
    dispatcher.include_router(build_router())
    return dispatcher


async def run_polling(settings: AdminSettings) -> None:
    """Run the admin bot until the process is stopped."""
    bot = create_bot(settings)
    database = Database()
    database.init(settings.db_path)
    context = AdminContext(AdminService(database), settings)
    dispatcher = create_dispatcher(context, settings)

    write_heartbeat(settings.db_path, "admin")
    heartbeat_task = asyncio.create_task(
        heartbeat_loop(settings.db_path, "admin"),
        name="admin-heartbeat",
    )

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_my_commands(BOT_COMMANDS)
        logger.info("admin_bot_started")
        await dispatcher.start_polling(bot)
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        await database.dispose()
        await bot.session.close()
        logger.info("admin_bot_stopped")


def _print_validation_errors(exc: ValidationError) -> None:
    """Print per-field configuration errors without echoing secrets."""
    for error in exc.errors(include_input=False):
        location = ".".join(str(part) for part in error["loc"])
        print(f"Configuration error: {location}: {error['msg']}", file=sys.stderr)


def main() -> None:
    """Load admin settings, configure logging and start polling."""
    try:
        settings = get_admin_settings()
    except ValidationError as exc:
        _print_validation_errors(exc)
        raise SystemExit(2) from exc

    setup_logging(settings.log_level, json_logs=settings.log_json)
    try:
        asyncio.run(run_polling(settings))
    except KeyboardInterrupt:
        logger.info("admin_bot_interrupted")


if __name__ == "__main__":
    main()
