"""Polling reminder scheduler for the polling bot process."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.core.logging import get_logger
from app.db.enums import ReminderKind
from app.services.reminders.service import DueReminder, ReminderService
from app.texts import ru

logger = get_logger(__name__)


class ReminderScheduler:
    def __init__(
        self,
        bot: Bot,
        service: ReminderService,
        *,
        interval_seconds: int = 60,
        batch_size: int = 100,
    ) -> None:
        if interval_seconds < 1:
            raise ValueError("interval_seconds must be positive")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._bot = bot
        self._service = service
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size

    async def tick(self, *, now: datetime | None = None) -> int:
        at = now or datetime.now(UTC)
        reminders = await self._service.due(now=at, limit=self._batch_size)
        sent = 0
        for reminder in reminders:
            try:
                await self._send(reminder)
            except TelegramAPIError as exc:
                logger.warning(
                    "reminder_send_failed",
                    reminder_id=reminder.id,
                    user_id=reminder.user_id,
                    kind=reminder.kind.value,
                    error=str(exc),
                )
            else:
                sent += 1
            await self._service.mark_sent(reminder.id, now=at)
        if reminders:
            logger.info("reminders_processed", due=len(reminders), sent=sent)
        return sent

    async def run_forever(self) -> None:
        logger.info(
            "reminder_scheduler_started",
            interval_seconds=self._interval_seconds,
            batch_size=self._batch_size,
        )
        try:
            while True:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("reminder_tick_failed")
                await asyncio.sleep(self._interval_seconds)
        finally:
            logger.info("reminder_scheduler_stopped")

    async def _send(self, reminder: DueReminder) -> None:
        text = {
            ReminderKind.SRS: ru.REMINDER_SRS,
            ReminderKind.DAILY: ru.REMINDER_DAILY,
        }.get(reminder.kind, ru.REMINDER_SRS)
        await self._bot.send_message(reminder.tg_id, text)
