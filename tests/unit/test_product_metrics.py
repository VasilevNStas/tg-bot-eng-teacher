from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.cli import main
from app.db.base import Base
from app.db.database import Database
from app.db.enums import (
    CEFRLevel,
    ExerciseSessionKind,
    ExerciseState,
    MessageRole,
)
from app.db.models import Conversation, ExerciseSession, Message, User
from app.services.product_metrics import (
    MAX_PERIOD_DAYS,
    ProductMetricsService,
    format_product_metrics,
)

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


def days_ago(days: int, *, hours: int = 0) -> datetime:
    return NOW - timedelta(days=days, hours=hours)


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def seed(database: Database) -> None:
    async with database.session() as session:
        first = User(
            tg_id=1,
            level=CEFRLevel.B1,
            onboarding_state="done",
            created_at=days_ago(10),
        )
        second = User(
            tg_id=2,
            level=CEFRLevel.A2,
            onboarding_state="new",
            created_at=days_ago(2),
        )
        idle = User(
            tg_id=3,
            level=CEFRLevel.A2,
            onboarding_state="done",
            created_at=days_ago(40),
        )
        session.add_all([first, second, idle])
        await session.flush()

        chat = Conversation(user_id=first.id, started_at=days_ago(7))
        session.add(chat)
        await session.flush()
        for when in (days_ago(7), days_ago(1), days_ago(0)):
            session.add(
                Message(
                    conversation_id=chat.id,
                    role=MessageRole.USER,
                    content="hello",
                    created_at=when,
                )
            )

        session.add_all(
            [
                ExerciseSession(
                    user_id=first.id,
                    kind=ExerciseSessionKind.DRILL,
                    meta={},
                    state=ExerciseState.FINISHED,
                    started_at=days_ago(6),
                    finished_at=days_ago(6),
                ),
                ExerciseSession(
                    user_id=first.id,
                    kind=ExerciseSessionKind.DRILL,
                    meta={},
                    state=ExerciseState.ABANDONED,
                    started_at=days_ago(4),
                    finished_at=days_ago(4),
                ),
                ExerciseSession(
                    user_id=first.id,
                    kind=ExerciseSessionKind.WORDS,
                    meta={},
                    state=ExerciseState.FINISHED,
                    started_at=days_ago(0),
                    finished_at=days_ago(0),
                ),
                ExerciseSession(
                    user_id=second.id,
                    kind=ExerciseSessionKind.DRILL,
                    meta={},
                    state=ExerciseState.FINISHED,
                    started_at=days_ago(0),
                    finished_at=days_ago(0),
                ),
            ]
        )


@pytest.mark.asyncio
async def test_product_metrics_snapshot(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed(database)

    report = await ProductMetricsService(database).build(days=7, now=NOW)

    assert report.total_users == 3
    assert report.new_users == 1
    assert report.active_users == 2
    assert report.wau == 2
    assert report.mau == 2
    assert [item.users for item in report.dau] == [1, 0, 1, 0, 0, 1, 2]

    assert report.retention.d1_eligible == 1
    assert report.retention.d1_retained == 1
    assert report.retention.d1_pct == 100
    assert report.retention.d7_eligible == 1
    assert report.retention.d7_retained == 1
    assert report.retention.d7_pct == 100

    by_kind = {item.kind: item for item in report.completion}
    assert by_kind[ExerciseSessionKind.DRILL].started == 3
    assert by_kind[ExerciseSessionKind.DRILL].finished == 2
    assert by_kind[ExerciseSessionKind.DRILL].abandoned == 1
    assert by_kind[ExerciseSessionKind.DRILL].pct == 67
    assert by_kind[ExerciseSessionKind.WORDS].finished == 1
    assert by_kind[ExerciseSessionKind.WORDS].pct == 100
    assert by_kind[ExerciseSessionKind.TRANSLATION].started == 0

    assert report.onboarding_done == 2
    assert report.onboarding_pct == 67
    assert report.user_messages == 2
    assert report.messages_per_active_user == 1.0

    await database.dispose()


@pytest.mark.asyncio
async def test_empty_database_has_zero_metrics(tmp_path: Path) -> None:
    database = await make_database(tmp_path)

    report = await ProductMetricsService(database).build(days=7, now=NOW)

    assert report.total_users == 0
    assert report.active_users == 0
    assert report.retention.d1_eligible == 0
    assert report.messages_per_active_user == 0.0
    assert all(item.users == 0 for item in report.dau)

    await database.dispose()


@pytest.mark.asyncio
async def test_rejects_invalid_period(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    service = ProductMetricsService(database)

    with pytest.raises(ValueError):
        await service.build(days=0, now=NOW)
    with pytest.raises(ValueError):
        await service.build(days=MAX_PERIOD_DAYS + 1, now=NOW)

    await database.dispose()


@pytest.mark.asyncio
async def test_as_dict_is_json_ready(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed(database)

    report = await ProductMetricsService(database).build(days=7, now=NOW)
    payload = report.as_dict()

    assert payload["period_days"] == 7
    assert payload["dau"][-1]["users"] == 2  # type: ignore[index]
    assert json.loads(json.dumps(payload))["total_users"] == 3

    await database.dispose()


@pytest.mark.asyncio
async def test_formatter_contains_sections(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await seed(database)

    report = await ProductMetricsService(database).build(days=7, now=NOW)
    text = format_product_metrics(report)

    assert "Product metrics" in text
    assert "DAU:" in text
    assert "Retention:" in text
    assert "Completion:" in text
    assert "drill" in text

    await database.dispose()


def test_cli_product_metrics_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "bot.db"
    database = Database()
    database.init(db_path)
    now = datetime.now(UTC)

    async def prepare() -> None:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            user = User(tg_id=1, level=CEFRLevel.B1, onboarding_state="done")
            session.add(user)
            await session.flush()
            chat = Conversation(user_id=user.id)
            session.add(chat)
            await session.flush()
            session.add(
                Message(
                    conversation_id=chat.id,
                    role=MessageRole.USER,
                    content="hello",
                    created_at=now,
                )
            )

    asyncio.run(prepare())
    monkeypatch.setenv("DB_PATH", str(db_path))

    assert main(["product-metrics", "--json"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output[output.index("{") :])

    assert payload["total_users"] == 1
    assert payload["active_users"] == 1
    assert payload["dau"][-1]["users"] == 1

    asyncio.run(database.dispose())
