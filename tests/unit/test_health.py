from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from app.db.base import Base
from app.db.database import Database
from app.db.enums import ContentKind
from app.services.health import (
    _head_revision,
    check_health,
    format_health,
    heartbeat_path,
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
