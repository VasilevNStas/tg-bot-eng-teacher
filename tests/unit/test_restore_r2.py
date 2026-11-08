from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "restore_r2.sh"


def write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def make_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE alembic_version(version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('test-revision')")
        connection.commit()
    finally:
        connection.close()


def make_stubs(tmp_path: Path) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rclone = bin_dir / "rclone"
    age = bin_dir / "age"
    db_sh = bin_dir / "db.sh"
    rclone_log = tmp_path / "rclone.log"
    db_log = tmp_path / "db.log"
    write_executable(
        rclone,
        """case "$1" in
  lsf)
    printf 'offsite-1.db;2026-11-01T00:00:00Z\\n'
    printf 'offsite-2.db;2026-11-02T00:00:00Z\\n'
    ;;
  copyto)
    cp "$RCLONE_SOURCE" "$3"
    ;;
  *)
    exit 1
    ;;
esac
printf '%s\\n' "$*" >> "$RCLONE_LOG"
""",
    )
    write_executable(
        age,
        """out=""; input=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) out="$2"; shift ;;
    --identity) shift ;;
    --decrypt) ;;
    *) input="$1" ;;
  esac
  shift
done
cp "$input" "$out"
""",
    )
    write_executable(
        db_sh,
        'printf \'%s\\n\' "$*" >> "$DB_LOG"\nexit "${DB_EXIT:-0}"\n',
    )
    return {
        "rclone": rclone,
        "age": age,
        "db_sh": db_sh,
        "rclone_log": rclone_log,
        "db_log": db_log,
    }


def run_script(
    tmp_path: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "source.db"
    make_database(source)
    stubs = make_stubs(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "BACKUP_REMOTE": "r2:test-bucket/backups",
            "RCLONE_BIN": str(stubs["rclone"]),
            "AGE_BIN": str(stubs["age"]),
            "DB_SH": str(stubs["db_sh"]),
            "RCLONE_SOURCE": str(source),
            "RCLONE_LOG": str(stubs["rclone_log"]),
            "DB_LOG": str(stubs["db_log"]),
        }
    )
    env.pop("AGE_IDENTITY", None)
    env.pop("DRILL_LOG", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_dry_run_selects_latest_without_download(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--dry-run")

    assert result.returncode == 0
    assert "offsite-2.db" in result.stdout
    assert "(dry-run)" in result.stdout
    assert "copyto" not in read(tmp_path / "rclone.log")


def test_drills_latest_object(tmp_path: Path) -> None:
    result = run_script(tmp_path)

    assert result.returncode == 0
    assert "rto_seconds:" in result.stdout
    assert "mode: drill only" in result.stdout
    log = read(tmp_path / "db.log")
    assert "drill" in log
    assert "smoke" in log
    assert "offsite-2.db" in log


def test_file_option_downloads_specific_object(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--file", "offsite-1.db")

    assert result.returncode == 0
    rclone_log = read(tmp_path / "rclone.log")
    assert "copyto" in rclone_log
    assert "offsite-1.db" in rclone_log
    assert "drill" in read(tmp_path / "db.log")


def test_apply_requires_yes(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--apply")

    assert result.returncode != 0
    assert "without --yes" in result.stderr


def test_apply_calls_restore(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--apply", "--yes")

    assert result.returncode == 0
    assert "restore" in read(tmp_path / "db.log")
    assert "applied:" in result.stdout


def test_decrypts_age_object(tmp_path: Path) -> None:
    result = run_script(
        tmp_path,
        "--file",
        "offsite-2.db.age",
        extra_env={"AGE_IDENTITY": str(tmp_path / "key.txt")},
    )

    assert result.returncode == 0
    log = read(tmp_path / "db.log")
    assert "restored.db" in log


def test_age_requires_identity(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--file", "offsite-2.db.age")

    assert result.returncode != 0
    assert "AGE_IDENTITY is required" in result.stderr


def test_missing_rclone_is_a_clear_error(tmp_path: Path) -> None:
    result = run_script(
        tmp_path,
        "--file",
        "offsite-1.db",
        extra_env={"RCLONE_BIN": str(tmp_path / "missing-rclone")},
    )

    assert result.returncode != 0
    assert "rclone is required" in result.stderr
