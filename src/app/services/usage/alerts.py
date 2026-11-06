"""Polling scheduler for project-wide LLM budget alerts."""

from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.core.logging import get_logger
from app.services.usage.service import BudgetAlert, UsageService
from app.texts import ru

logger = get_logger(__name__)


class BudgetAlertScheduler:
    """Send 80%/100% monthly budget alerts to the admin chat."""

    def __init__(
        self,
        bot: Bot,
        usage: UsageService,
        *,
        chat_id: int | None,
        interval_seconds: int = 300,
    ) -> None:
        if interval_seconds < 1:
            raise ValueError("interval_seconds must be positive")
        self._bot = bot
        self._usage = usage
        self._chat_id = chat_id
        self._interval_seconds = interval_seconds

    async def tick(self) -> bool:
        """Send at most one pending threshold alert."""
        alert = await self._usage.pending_budget_alert()
        if alert is None:
            return False
        chat_id = self._chat_id
        if chat_id is None:
            logger.warning(
                "budget_alert_no_chat",
                percent=alert.threshold_percent,
                spent_usd=str(alert.spent_usd),
                budget_usd=str(alert.budget_usd),
            )
            self._usage.mark_budget_alerted(alert)
            return False

        try:
            await self._send(alert, chat_id)
        except TelegramAPIError as exc:
            logger.warning(
                "budget_alert_send_failed",
                percent=alert.threshold_percent,
                error=str(exc),
            )
            return False
        self._usage.mark_budget_alerted(alert)
        logger.warning(
            "budget_alert_sent",
            percent=alert.threshold_percent,
            spent_usd=str(alert.spent_usd),
            budget_usd=str(alert.budget_usd),
        )
        return True

    async def run_forever(self) -> None:
        """Loop until cancelled, surviving individual tick failures."""
        logger.info(
            "budget_alert_scheduler_started",
            interval_seconds=self._interval_seconds,
        )
        try:
            while True:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("budget_alert_tick_failed")
                await asyncio.sleep(self._interval_seconds)
        finally:
            logger.info("budget_alert_scheduler_stopped")

    async def _send(self, alert: BudgetAlert, chat_id: int) -> None:
        text = ru.BUDGET_ALERT.format(
            percent=alert.threshold_percent,
            spent=alert.spent_usd,
            budget=alert.budget_usd,
        )
        await self._bot.send_message(chat_id, text)
