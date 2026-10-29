from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.cli import main
from app.core.logging import setup_logging
from app.db.base import Base
from app.db.database import Database
from app.db.models import ContentItem

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "sample.yaml"


async def prepare_database(path: Path) -> None:
    database = Database()
    database.init(path)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await database.dispose()


async def item_count(path: Path) -> int:
    database = Database()
    database.init(path)
    try:
        async with database.session() as session:
            value = await session.scalar(select(func.count()).select_from(ContentItem))
        return int(value or 0)
    finally:
        await database.dispose()


def test_cli_imports_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "bot.db"
    asyncio.run(prepare_database(db_path))
    monkeypatch.setenv("DB_PATH", str(db_path))

    assert main(["import-content", str(FIXTURE)]) == 0
    assert asyncio.run(item_count(db_path)) == 5

    assert main(["import-content", str(FIXTURE)]) == 0
    assert asyncio.run(item_count(db_path)) == 5


def test_cli_dry_run_does_not_touch_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "bot.db"
    monkeypatch.setenv("DB_PATH", str(db_path))

    assert main(["import-content", "--dry-run", str(FIXTURE)]) == 0
    assert not db_path.exists()


def test_cli_returns_error_for_invalid_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("items: []", encoding="utf-8")

    assert main(["import-content", str(path)]) == 1
    assert "error:" in capsys.readouterr().err


def test_cli_content_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "bot.db"
    setup_logging("WARNING", stream=sys.stderr)
    asyncio.run(prepare_database(db_path))
    monkeypatch.setenv("DB_PATH", str(db_path))
    assert main(["import-content", str(FIXTURE)]) == 0
    capsys.readouterr()

    assert main(["content-report"]) == 0
    output = capsys.readouterr().out
    assert "Content coverage" in output
    assert "test-topic-a1 | A1 | irregular_verb | 1" in output
    assert "license unknown: 5" in output

    assert main(["content-report", "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.out, f"stdout empty; stderr={captured.err!r}"
    assert captured.out.lstrip().startswith("{"), repr(captured.out[:120])
    payload = json.loads(captured.out)
    assert payload["active_items"] == 5
    assert payload["memberships"] == 5
    assert payload["duplicates"] == []
