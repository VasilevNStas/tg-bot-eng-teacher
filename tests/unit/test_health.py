from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from app.db.base import Base
from app.db.database import Database
from app.db.enums import ContentKind
from app.services.health import (
    _head_revision,
    check_health,
    format_health,
    heartbeat_loop,
    heartbeat_path,
    ping_uptime,
    write_heartbeat,
)


async def make_database(tmp_path: Path, *, with_content: bool = True) -> Path:
    db_path = tmp_path / "bot.db"
    database = Database()
    database.init(db_path)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        await connection.execute(
            text("INSERT INTO alembic_version VALUES (:version)"),
            {"version": _head_revision()},
        )
        if with_content:
            await connection.execute(
                text(
                    "INSERT INTO topics (slug, title, created_at, updated_at) "
                    "VALUES ('t', 'T', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO content_items "
                    "(slug, topic_id, is_active, kind, payload, license, "
                    "created_at, updated_at) "
                    "VALUES ('i', 1, 1, :kind, :payload, 'unknown', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"kind": ContentKind.VOCABULARY.value, "payload": "{}"},
            )
    await database.dispose()
    return db_path


async def test_healthy_database(tmp_path: Path) -> None:
    db_path = await make_database(tmp_path)
    write_heartbeat(db_path, "bot")

    report = await check_health(db_path, "bot")

    assert report.ok, format_health(report)
    names = {check.name for check in report.checks}
    assert {"database", "migration", "content", "heartbeat", "disk"} <= names


async def test_missing_heartbeat_fails(tmp_path: Path) -> None:
    db_path = await make_database(tmp_path)

    report = await check_health(db_path, "bot")

    assert not report.ok
    heartbeat = next(check for check in report.checks if check.name == "heartbeat")
    assert not heartbeat.ok


async def test_empty_content_fails(tmp_path: Path) -> None:
    db_path = await make_database(tmp_path, with_content=False)
    write_heartbeat(db_path, "bot")

    report = await check_health(db_path, "bot")

    assert not report.ok
    content = next(check for check in report.checks if check.name == "content")
    assert not content.ok


async def test_missing_database_fails(tmp_path: Path) -> None:
    report = await check_health(tmp_path / "missing.db", "bot")

    assert not report.ok
    assert report.checks[0].name == "database"
    assert not report.checks[0].ok


async def test_heartbeat_path_is_next_to_database(tmp_path: Path) -> None:
    path = heartbeat_path(tmp_path / "bot.db", "admin")

    assert path == tmp_path / "health" / "admin.heartbeat"


async def test_ping_uptime_success() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await ping_uptime("https://hc-ping.com/abc", client=client)


async def test_ping_uptime_reports_failure() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    async with httpx.AsyncClient(transport=transport) as client:
        assert not await ping_uptime("https://hc-ping.com/abc", client=client)


async def test_ping_uptime_swallows_network_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert not await ping_uptime("https://hc-ping.com/abc", client=client)


class _RecordingPingClient:
    def __init__(self) -> None:
        self.calls = 0

    async def get(self, url: str, *, timeout: float | None = None) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200)

    async def aclose(self) -> None:
        return None


async def test_heartbeat_loop_pings_uptime(tmp_path: Path) -> None:
    client = _RecordingPingClient()
    task = asyncio.create_task(
        heartbeat_loop(
            tmp_path / "bot.db",
            "bot",
            interval_seconds=1,
            ping_url="https://hc-ping.com/abc",
            ping_client=client,  # type: ignore[arg-type]
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.calls >= 1
    assert heartbeat_path(tmp_path / "bot.db", "bot").is_file()
