"""Application entry point."""

from __future__ import annotations

import asyncio
import sys

from aiogram.exceptions import TelegramAPIError
from pydantic import ValidationError

from app.bot.commands import BOT_COMMANDS
from app.bot.factory import create_bot, create_dispatcher
from app.bot.middlewares.dedupe import prune_processed_updates
from app.core.config import BotMode, Settings, get_settings
from app.core.di import build_services
from app.core.logging import get_logger, setup_logging
from app.db.database import Database

logger = get_logger(__name__)


async def run_polling(settings: Settings) -> None:
    bot = create_bot(settings)
    database = Database()
    database.init(settings.db_path)
    services = build_services(database, settings)
    dispatcher = create_dispatcher(services)

    removed = await prune_processed_updates(
        database,
        older_than_days=settings.processed_updates_retention_days,
    )
    if removed:
        logger.info("processed_updates_pruned", count=removed)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        try:
            await bot.set_my_commands(BOT_COMMANDS)
        except TelegramAPIError as exc:
            logger.warning("set_commands_failed", error=str(exc))
        logger.info("bot_started", mode=settings.bot_mode.value)
        await dispatcher.start_polling(bot)
    finally:
        await services.aclose()
        await database.dispose()
        await bot.session.close()
        logger.info("bot_stopped")


def _print_validation_errors(exc: ValidationError) -> None:
    for error in exc.errors(include_input=False):
        location = ".".join(str(part) for part in error["loc"])
        print(f"Configuration error: {location}: {error['msg']}", file=sys.stderr)


def main() -> None:
    try:
        settings = get_settings()
    except ValidationError as exc:
        _print_validation_errors(exc)
        raise SystemExit(2) from exc

    setup_logging(settings.log_level, json_logs=settings.log_json)

    if settings.bot_mode is BotMode.WEBHOOK:
        logger.error("webhook_mode_not_implemented", hint="use BOT_MODE=polling (ADR-013)")
        raise SystemExit(2)

    try:
        asyncio.run(run_polling(settings))
    except KeyboardInterrupt:
        logger.info("bot_interrupted")


if __name__ == "__main__":
    main()
