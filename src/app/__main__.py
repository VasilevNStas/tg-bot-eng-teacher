"""Application entry point."""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress

from aiogram.exceptions import TelegramAPIError
from pydantic import ValidationError

from app.bot.commands import BOT_COMMANDS
from app.bot.factory import create_bot, create_dispatcher
from app.bot.middlewares.dedupe import prune_processed_updates
from app.core.config import BotMode, Settings, get_settings
from app.core.di import build_services
from app.core.logging import get_logger, setup_logging
from app.core.sentry import setup_sentry, shutdown_sentry
from app.db.database import Database
from app.services.health import heartbeat_loop
from app.services.metrics import metrics_loop
from app.services.privacy.service import PrivacyService
from app.services.reminders.scheduler import ReminderScheduler
from app.services.reminders.service import ReminderService
from app.services.usage.alerts import BudgetAlertScheduler

logger = get_logger(__name__)


async def run_polling(settings: Settings) -> None:
    """Start polling, the reminder scheduler, and periodic cleanup."""
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

    pruned = await PrivacyService(database).prune_history(settings.history_retention_days)
    if pruned:
        logger.info("history_pruned", count=pruned)

    reminder_task = asyncio.create_task(
        ReminderScheduler(
            bot,
            ReminderService(database),
            interval_seconds=settings.reminder_poll_seconds,
            batch_size=settings.reminder_batch_size,
        ).run_forever(),
        name="reminder-scheduler",
    )
    uptime_url = (
        settings.uptime_ping_url.get_secret_value().strip()
        if settings.uptime_ping_url is not None
        else ""
    )
    heartbeat_task = asyncio.create_task(
        heartbeat_loop(
            settings.db_path,
            "bot",
            ping_url=uptime_url or None,
            ping_timeout_seconds=settings.uptime_ping_timeout_seconds,
        ),
        name="heartbeat",
    )
    budget_task = asyncio.create_task(
        BudgetAlertScheduler(
            bot,
            services.usage,
            chat_id=settings.admin_alert_chat_id,
            interval_seconds=settings.budget_alert_poll_seconds,
        ).run_forever(),
        name="budget-alerts",
    )
    metrics_task = asyncio.create_task(
        metrics_loop(
            settings.db_path,
            services.metrics,
            interval_seconds=settings.metrics_snapshot_seconds,
        ),
        name="metrics",
    )

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        try:
            await bot.set_my_commands(BOT_COMMANDS)
        except TelegramAPIError as exc:
            logger.warning("set_commands_failed", error=str(exc))
        logger.info("bot_started", mode=settings.bot_mode.value)
        await dispatcher.start_polling(bot)
    finally:
        reminder_task.cancel()
        heartbeat_task.cancel()
        budget_task.cancel()
        metrics_task.cancel()
        with suppress(asyncio.CancelledError):
            await reminder_task
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        with suppress(asyncio.CancelledError):
            await budget_task
        with suppress(asyncio.CancelledError):
            await metrics_task
        await services.aclose()
        await database.dispose()
        await bot.session.close()
        logger.info("bot_stopped")


def _print_validation_errors(exc: ValidationError) -> None:
    """Print concise per-field configuration errors without echoing secrets."""
    for error in exc.errors(include_input=False):
        location = ".".join(str(part) for part in error["loc"])
        print(f"Configuration error: {location}: {error['msg']}", file=sys.stderr)


def main() -> None:
    """Load settings, set up logging, and run the bot until interrupted."""
    try:
        settings = get_settings()
    except ValidationError as exc:
        _print_validation_errors(exc)
        raise SystemExit(2) from exc

    setup_logging(settings.log_level, json_logs=settings.log_json)
    setup_sentry(settings, service="bot")

    if settings.bot_mode is BotMode.WEBHOOK:
        logger.error("webhook_mode_not_implemented", hint="use BOT_MODE=polling (ADR-013)")
        raise SystemExit(2)

    try:
        asyncio.run(run_polling(settings))
    except KeyboardInterrupt:
        logger.info("bot_interrupted")
    finally:
        shutdown_sentry()


if __name__ == "__main__":
    main()
