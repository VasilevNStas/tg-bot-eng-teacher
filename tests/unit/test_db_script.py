from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "db.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("sqlite3") is None,
    reason="sqlite3 CLI is required for db.sh tests",
)


def run(
    db_path: Path,
    backup_dir: Path,
    *args: str,
    check: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DB_PATH"] = str(db_path)
    env["BACKUP_DIR"] = str(backup_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT), "--local", *args],
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


def make_database(path: Path, marker: str = "initial") -> None:
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE alembic_version(version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('test-revision')")
        connection.execute("CREATE TABLE marker(value TEXT)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))
        connection.execute("CREATE TABLE users(id INTEGER)")
        connection.execute("CREATE TABLE content_items(id INTEGER, is_active INTEGER)")
        connection.execute("CREATE TABLE exercise_sessions(id INTEGER)")
        connection.execute("CREATE TABLE exercise_attempts(id INTEGER)")
        connection.execute("CREATE TABLE word_progress(id INTEGER)")
        connection.commit()
    finally:
        connection.close()


def marker_value(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT value FROM marker").fetchone()
        assert row is not None
        return str(row[0])
    finally:
        connection.close()


def test_status_reports_database(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    make_database(db_path)

    result = run(db_path, tmp_path / "backups", "status")

    assert str(db_path) in result.stdout
    assert "integrity: ok" in result.stdout
    assert "revision: test-revision" in result.stdout


def test_backup_rotates_old_files(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    backup_dir = tmp_path / "backups"
    make_database(db_path)

    for index in range(5):
        run(db_path, backup_dir, "backup", f"smoke{index}", extra_env={"BACKUP_RETENTION": "3"})

    assert len(list(backup_dir.glob("*.db"))) == 3


def test_reset_requires_yes(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    make_database(db_path)

    result = run(db_path, tmp_path / "backups", "reset", check=False)

    assert result.returncode != 0
    assert "without --yes" in result.stderr
    assert db_path.exists()


def test_reset_removes_database_and_wal_files(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    backup_dir = tmp_path / "backups"
    make_database(db_path)
    db_path.with_name(db_path.name + "-wal").write_text("", encoding="utf-8")
    db_path.with_name(db_path.name + "-shm").write_text("", encoding="utf-8")

    run(db_path, backup_dir, "reset", "--yes", "--no-start")

    assert not db_path.exists()
    assert not db_path.with_name(db_path.name + "-wal").exists()
    assert not db_path.with_name(db_path.name + "-shm").exists()
    assert list(backup_dir.glob("pre-reset-*.db"))


def test_reset_production_requires_force(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    make_database(db_path)

    result = run(
        db_path,
        tmp_path / "backups",
        "reset",
        "--yes",
        check=False,
        extra_env={"APP_ENV": "production"},
    )

    assert result.returncode != 0
    assert "--force" in result.stderr
    assert db_path.exists()


def test_restore_replaces_database(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    backup_dir = tmp_path / "backups"
    make_database(db_path, "original")

    backup = Path(run(db_path, backup_dir, "backup", "manual").stdout.strip().splitlines()[-1])
    assert backup.exists()
    assert marker_value(backup) == "original"

    make_database(db_path, "modified")
    assert marker_value(db_path) == "modified"

    run(db_path, backup_dir, "restore", str(backup), "--no-start")

    assert marker_value(db_path) == "original"
    assert list(backup_dir.glob("pre-restore-*.db"))


def test_restore_rejects_corrupt_file(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    make_database(db_path, "untouched")
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("not a database", encoding="utf-8")

    result = run(db_path, tmp_path / "backups", "restore", str(corrupt), check=False)

    assert result.returncode != 0
    assert "integrity check failed" in result.stderr
    assert marker_value(db_path) == "untouched"


def test_drill_verifies_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    backup_dir = tmp_path / "backups"
    make_database(db_path)

    backup = Path(run(db_path, backup_dir, "backup", "manual").stdout.strip())
    result = run(db_path, backup_dir, "drill", str(backup))

    assert "integrity: ok" in result.stdout
    assert "revision: test-revision" in result.stdout
    assert "content_items: 0" in result.stdout
    assert "log:" in result.stdout
    drill_log = backup_dir / "drills.log"
    assert drill_log.exists()
    assert "ok source=" in drill_log.read_text(encoding="utf-8")
    assert "duration_s=" in drill_log.read_text(encoding="utf-8")
    assert "duration_s:" in result.stdout


def test_drill_uses_latest_backup_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    backup_dir = tmp_path / "backups"
    make_database(db_path)
    run(db_path, backup_dir, "backup", "manual")

    result = run(db_path, backup_dir, "drill")

    assert "integrity: ok" in result.stdout


def test_drill_rejects_corrupt_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    corrupt = backup_dir / "corrupt.db"
    corrupt.write_text("not a database", encoding="utf-8")

    result = run(db_path, backup_dir, "drill", str(corrupt), check=False)

    assert result.returncode != 0
    assert "integrity check failed" in result.stderr


def test_drill_fails_without_backups(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"

    result = run(db_path, tmp_path / "backups", "drill", check=False)

    assert result.returncode != 0
    assert "no backups found" in result.stderr


def test_smoke_reports_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    make_database(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("INSERT INTO users VALUES (1)")
        connection.execute("INSERT INTO content_items VALUES (1, 1)")
        connection.execute("INSERT INTO exercise_sessions VALUES (1)")
        connection.execute("INSERT INTO exercise_attempts VALUES (1)")
        connection.execute("INSERT INTO word_progress VALUES (1)")
        connection.commit()
    finally:
        connection.close()

    result = run(db_path, tmp_path / "backups", "smoke", str(db_path))

    assert "smoke: ok" in result.stdout
    assert "users: 1" in result.stdout
    assert "content_items: 1 active: 1" in result.stdout
    assert "exercise_attempts: 1" in result.stdout
    assert "word_progress: 1" in result.stdout


def test_smoke_rejects_corrupt_database(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("not a database", encoding="utf-8")

    result = run(tmp_path / "bot.db", tmp_path / "backups", "smoke", str(corrupt), check=False)

    assert result.returncode != 0
    assert "integrity check failed" in result.stderr


def test_smoke_fails_without_backups(tmp_path: Path) -> None:
    result = run(tmp_path / "bot.db", tmp_path / "backups", "smoke", check=False)

    assert result.returncode != 0
    assert "no backups found" in result.stderr
