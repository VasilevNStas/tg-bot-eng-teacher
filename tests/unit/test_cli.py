from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
import yaml
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


async def active_item_count(path: Path) -> int:
    database = Database()
    database.init(path)
    try:
        async with database.session() as session:
            value = await session.scalar(
                select(func.count()).select_from(ContentItem).where(ContentItem.is_active.is_(True))
            )
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


def vocabulary_doc(entries: list[dict[str, object]]) -> dict[str, object]:
    return {
        "topic": {"slug": "test-words", "title": "Test words"},
        "source": "test",
        "license": "unknown",
        "items": entries,
    }


def test_cli_imports_vocabulary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bank_path = tmp_path / "core_words_0001_0002.yaml"
    bank_path.write_text(
        yaml.safe_dump(
            vocabulary_doc(
                [
                    {
                        "slug": "word-be",
                        "kind": "vocabulary",
                        "term": "be",
                        "translation_ru": "быть",
                        "frequency": 100,
                        "frequency_rank": 1,
                    },
                    {
                        "slug": "word-and",
                        "kind": "vocabulary",
                        "term": "and",
                        "translation_ru": "и",
                        "frequency": 90,
                        "frequency_rank": 2,
                    },
                ]
            ),
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "bot.db"
    asyncio.run(prepare_database(db_path))
    monkeypatch.setenv("DB_PATH", str(db_path))

    assert main(["import-content", str(bank_path)]) == 0
    assert asyncio.run(item_count(db_path)) == 2

    assert main(["import-content", str(bank_path)]) == 0
    assert asyncio.run(item_count(db_path)) == 2


def test_cli_import_vocabulary_dry_run_does_not_touch_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank_path = tmp_path / "core_words_0001_0001.yaml"
    bank_path.write_text(
        yaml.safe_dump(
            vocabulary_doc(
                [
                    {
                        "slug": "word-be",
                        "kind": "vocabulary",
                        "term": "be",
                        "translation_ru": "быть",
                        "frequency": 100,
                        "frequency_rank": 1,
                    }
                ]
            ),
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "bot.db"
    monkeypatch.setenv("DB_PATH", str(db_path))

    assert main(["import-content", "--dry-run", str(bank_path)]) == 0
    assert not db_path.exists()


def test_cli_seed_imports_bundled_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = tmp_path / "content"
    topics = content / "topics"
    vocabulary = content / "vocabulary"
    topics.mkdir(parents=True)
    vocabulary.mkdir()
    (topics / "sample.yaml").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    (vocabulary / "words.yaml").write_text(
        yaml.safe_dump(
            vocabulary_doc(
                [
                    {
                        "slug": "word-be",
                        "kind": "vocabulary",
                        "term": "be",
                        "translation_ru": "быть",
                        "frequency": 100,
                        "frequency_rank": 1,
                    },
                    {
                        "slug": "word-and",
                        "kind": "vocabulary",
                        "term": "and",
                        "translation_ru": "и",
                        "frequency": 90,
                        "frequency_rank": 2,
                    },
                ]
            ),
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    db_path = tmp_path / "bot.db"
    asyncio.run(prepare_database(db_path))
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("CONTENT_DIR", str(content))

    assert main(["seed"]) == 0
    assert asyncio.run(item_count(db_path)) == 7

    assert main(["seed"]) == 0
    assert asyncio.run(item_count(db_path)) == 7


def test_cli_seed_prune_deactivates_removed_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    content = tmp_path / "content"
    topics = content / "topics"
    vocabulary = content / "vocabulary"
    topics.mkdir(parents=True)
    vocabulary.mkdir()
    (topics / "sample.yaml").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    def write_words(words: list[dict[str, object]]) -> None:
        (vocabulary / "words.yaml").write_text(
            yaml.safe_dump(vocabulary_doc(words), allow_unicode=True),
            encoding="utf-8",
        )

    both: list[dict[str, object]] = [
        {"slug": "word-be", "kind": "vocabulary", "term": "be", "translation_ru": "быть"},
        {"slug": "word-and", "kind": "vocabulary", "term": "and", "translation_ru": "и"},
    ]
    write_words(both)

    db_path = tmp_path / "bot.db"
    asyncio.run(prepare_database(db_path))
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("CONTENT_DIR", str(content))

    assert main(["seed"]) == 0
    assert asyncio.run(active_item_count(db_path)) == 7

    write_words(both[:1])

    assert main(["seed", "--dry-run", "--prune"]) == 0
    assert asyncio.run(active_item_count(db_path)) == 7

    assert main(["seed", "--prune"]) == 0
    assert "deactivated=1" in capsys.readouterr().out
    assert asyncio.run(active_item_count(db_path)) == 6
    assert asyncio.run(item_count(db_path)) == 7

    assert main(["seed", "--prune"]) == 0
    assert "deactivated=0" in capsys.readouterr().out
    assert asyncio.run(active_item_count(db_path)) == 6


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
    assert "test-topic-a1 | irregular_verb | 1" in output
    assert "license unknown: 5" in output

    assert main(["content-report", "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.out, f"stdout empty; stderr={captured.err!r}"
    assert captured.out.lstrip().startswith("{"), repr(captured.out[:120])
    payload = json.loads(captured.out)
    assert payload["active_items"] == 5
    assert payload["duplicates"] == []


def test_cli_prune_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "bot.db"
    setup_logging("WARNING", stream=sys.stderr)
    asyncio.run(prepare_database(db_path))
    monkeypatch.setenv("DB_PATH", str(db_path))

    assert main(["prune-history", "--days", "0"]) == 0
    assert "deleted conversations: 0" in capsys.readouterr().out
