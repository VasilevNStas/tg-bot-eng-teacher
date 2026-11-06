from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.db.base import Base
from app.db.database import Database
from app.db.models import UsageLedger, User
from app.services.usage.service import (
    DailyBudgetExceeded,
    GlobalBudgetExceeded,
    UsageService,
    utc_day_start,
    utc_month_start,
)


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def make_user(database: Database, tg_id: int = 1) -> User:
    async with database.session() as session:
        user = User(tg_id=tg_id)
        session.add(user)
    return user


async def test_record_updates_daily_total(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = UsageService(database, daily_budget=1000)

    entry = await service.record(
        user_id=user.id,
        model="vendor/model",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0.001"),
        prompt_version="abc123",
    )

    assert entry.id is not None
    assert await service.spent_today(user.id) == 15
    snapshot = await service.snapshot(user.id)
    assert snapshot.spent_tokens == 15
    assert snapshot.remaining_tokens == 985

    await database.dispose()


async def test_old_and_foreign_usage_is_ignored(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database, tg_id=1)
    other = await make_user(database, tg_id=2)
    service = UsageService(database, daily_budget=1000)

    async with database.session() as session:
        session.add(
            UsageLedger(
                user_id=user.id,
                model="vendor/model",
                input_tokens=100,
                output_tokens=0,
                cost_usd=Decimal("0"),
                created_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
    await service.record(
        user_id=other.id,
        model="vendor/model",
        input_tokens=50,
        output_tokens=50,
        cost_usd=Decimal("0"),
    )

    assert await service.spent_today(user.id) == 0

    await database.dispose()


async def test_budget_check_counts_requested_tokens(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = UsageService(database, daily_budget=100)
    await service.record(
        user_id=user.id,
        model="vendor/model",
        input_tokens=60,
        output_tokens=30,
        cost_usd=Decimal("0"),
    )

    snapshot = await service.ensure_available(user.id, estimated_tokens=10)
    assert snapshot.spent_tokens == 90

    with pytest.raises(DailyBudgetExceeded) as error:
        await service.ensure_available(user.id, estimated_tokens=11)

    assert error.value.spent == 90
    assert error.value.budget == 100
    assert error.value.requested == 11

    await database.dispose()


async def test_negative_values_are_rejected(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = UsageService(database, daily_budget=100)

    with pytest.raises(ValueError):
        UsageService(database, daily_budget=0)
    with pytest.raises(ValueError):
        await service.ensure_available(user.id, estimated_tokens=-1)
    with pytest.raises(ValueError):
        await service.record(
            user_id=user.id,
            model="vendor/model",
            input_tokens=-1,
            output_tokens=0,
            cost_usd=Decimal("0"),
        )

    await database.dispose()


def test_utc_day_start_is_midnight() -> None:
    moment = datetime(2026, 10, 29, 15, 30, tzinfo=UTC)

    assert utc_day_start(moment) == datetime(2026, 10, 29, tzinfo=UTC)


async def test_monthly_spend_and_global_budget(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = UsageService(database, daily_budget=10_000, monthly_budget_usd=Decimal("1.00"))
    await service.record(
        user_id=user.id,
        model="vendor/model",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0.40"),
    )
    await service.record(
        user_id=user.id,
        model="vendor/model",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0.35"),
    )

    snapshot = await service.ensure_global_available(estimated_cost_usd=Decimal("0.20"))
    assert snapshot.spent_usd == Decimal("0.75")
    assert snapshot.budget_usd == Decimal("1.00")
    assert snapshot.remaining_usd == Decimal("0.25")

    with pytest.raises(GlobalBudgetExceeded) as error:
        await service.ensure_global_available(estimated_cost_usd=Decimal("0.30"))

    assert error.value.spent_usd == Decimal("0.75")
    assert error.value.requested_usd == Decimal("0.30")

    await database.dispose()


async def test_missing_provider_cost_is_estimated_from_price_table(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = UsageService(database, daily_budget=10_000)

    entry = await service.record(
        user_id=user.id,
        model="mistralai/mistral-nemo",
        input_tokens=1_000_000,
        output_tokens=0,
        cost_usd=Decimal("0"),
    )

    assert entry.cost_usd == Decimal("0.029")
    assert await service.spent_this_month() == Decimal("0.029")

    await database.dispose()


async def test_budget_alerts_fire_once_per_threshold(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    user = await make_user(database)
    service = UsageService(database, daily_budget=10_000, monthly_budget_usd=Decimal("1.00"))
    await service.record(
        user_id=user.id,
        model="vendor/model",
        input_tokens=1,
        output_tokens=0,
        cost_usd=Decimal("0.81"),
    )

    alert = await service.pending_budget_alert()
    assert alert is not None
    assert alert.threshold_percent == 80
    assert await service.pending_budget_alert() is not None

    service.mark_budget_alerted(alert)
    assert await service.pending_budget_alert() is None

    await service.record(
        user_id=user.id,
        model="vendor/model",
        input_tokens=1,
        output_tokens=0,
        cost_usd=Decimal("0.20"),
    )
    alert = await service.pending_budget_alert()
    assert alert is not None
    assert alert.threshold_percent == 100
    service.mark_budget_alerted(alert)
    assert await service.pending_budget_alert() is None

    await database.dispose()


def test_utc_month_start_is_first_day() -> None:
    moment = datetime(2026, 10, 29, 15, 30, tzinfo=UTC)

    assert utc_month_start(moment) == datetime(2026, 10, 1, tzinfo=UTC)
