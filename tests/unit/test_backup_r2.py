from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup_r2.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("sqlite3") is None,
    reason="sqlite3 CLI is required for backup_r2.sh tests",
)


def write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def make_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE alembic_version(version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('test-revision')")
        connection.execute("CREATE TABLE marker(value TEXT)")
        connection.execute("INSERT INTO marker VALUES ('x')")
        connection.commit()
    finally:
        connection.close()


def make_stubs(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rclone = bin_dir / "rclone"
    age = bin_dir / "age"
    log = tmp_path / "rclone.log"
    write_executable(
        rclone,
        'printf \'%s\\n\' "$*" >> "$RCLONE_LOG"\nexit "${RCLONE_EXIT:-0}"\n',
    )
    write_executable(
        age,
        """out=""; input=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) out="$2"; shift ;;
    --recipient) shift ;;
    --encrypt) ;;
    *) input="$1" ;;
  esac
  shift
done
cp "$input" "$out"
""",
    )
    return rclone, age, log


def run_script(
    tmp_path: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    db_path = tmp_path / "bot.db"
    make_database(db_path)
    rclone, age, log = make_stubs(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "DB_PATH": str(db_path),
            "BACKUP_DIR": str(tmp_path / "backups"),
            "BACKUP_REMOTE": "r2:test-bucket/backups",
            "RCLONE_BIN": str(rclone),
            "AGE_BIN": str(age),
            "RCLONE_LOG": str(log),
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def logs(tmp_path: Path) -> str:
    log = tmp_path / "rclone.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_dry_run_creates_local_backup_without_upload(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--dry-run")

    assert result.returncode == 0
    assert "would upload" in result.stderr
    assert "(dry-run)" in result.stdout
    assert list((tmp_path / "backups").glob("offsite-*.db"))
    assert logs(tmp_path) == ""


def test_upload_calls_rclone_copyto_and_verifies(tmp_path: Path) -> None:
    result = run_script(tmp_path)

    assert result.returncode == 0
    assert "(uploaded)" in result.stdout
    log = logs(tmp_path)
    assert "copyto" in log
    assert "lsf" in log
    assert "r2:test-bucket/backups/offsite-" in log


def test_upload_failure_is_reported(tmp_path: Path) -> None:
    result = run_script(tmp_path, extra_env={"RCLONE_EXIT": "1"})

    assert result.returncode != 0


def test_encrypts_before_upload(tmp_path: Path) -> None:
    result = run_script(tmp_path, extra_env={"BACKUP_AGE_RECIPIENT": "age1test"})

    assert result.returncode == 0
    assert "encrypt with age" in result.stderr
    assert list((tmp_path / "backups").glob("offsite-*.db.age"))
    assert ".db.age" in logs(tmp_path)
    assert ".db.age" in result.stdout


def test_no_encrypt_keeps_plain_database(tmp_path: Path) -> None:
    result = run_script(
        tmp_path,
        "--no-encrypt",
        extra_env={"BACKUP_AGE_RECIPIENT": "age1test"},
    )

    assert result.returncode == 0
    assert not list((tmp_path / "backups").glob("*.age"))
    assert ".db.age" not in logs(tmp_path)


def test_missing_rclone_is_a_clear_error(tmp_path: Path) -> None:
    result = run_script(
        tmp_path,
        extra_env={"RCLONE_BIN": str(tmp_path / "missing-rclone")},
    )

    assert result.returncode != 0
    assert "rclone is required" in result.stderr


def test_prune_remote_dry_run_does_not_call_rclone(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--dry-run", "--prune-remote")

    assert result.returncode == 0
    assert "would delete remote objects" in result.stderr
    assert logs(tmp_path) == ""
