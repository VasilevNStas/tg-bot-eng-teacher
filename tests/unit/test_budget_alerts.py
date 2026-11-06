from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.db.base import Base
from app.db.database import Database
from app.db.models import User
from app.services.usage.alerts import BudgetAlertScheduler
from app.services.usage.service import UsageService


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def make_spender(database: Database, *, cost: str = "0.81") -> UsageService:
    async with database.session() as session:
        user = User(tg_id=1)
        session.add(user)
        await session.flush()
        user_id = user.id
    usage = UsageService(database, daily_budget=10_000, monthly_budget_usd=Decimal("1.00"))
    await usage.record(
        user_id=user_id,
        model="vendor/model",
        input_tokens=1,
        output_tokens=0,
        cost_usd=Decimal(cost),
    )
    return usage


async def test_scheduler_sends_threshold_alert_once(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    usage = await make_spender(database)
    bot = FakeBot()
    scheduler = BudgetAlertScheduler(bot, usage, chat_id=123, interval_seconds=1)

    assert await scheduler.tick() is True
    assert len(bot.messages) == 1
    chat_id, text = bot.messages[0]
    assert chat_id == 123
    assert "80%" in text
    assert "$0.81" in text

    assert await scheduler.tick() is False
    assert len(bot.messages) == 1

    await database.dispose()


async def test_scheduler_marks_alert_without_chat_id(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    usage = await make_spender(database)
    bot = FakeBot()
    scheduler = BudgetAlertScheduler(bot, usage, chat_id=None, interval_seconds=1)

    assert await scheduler.tick() is False
    assert bot.messages == []
    assert await usage.pending_budget_alert() is None

    await database.dispose()
