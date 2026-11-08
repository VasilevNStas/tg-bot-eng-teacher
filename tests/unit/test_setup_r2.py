from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_r2.sh"


def write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def make_stub(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rclone = bin_dir / "rclone"
    log = tmp_path / "rclone.log"
    write_executable(
        rclone,
        'printf \'%s\\n\' "$*" >> "$RCLONE_LOG"\nexit "${RCLONE_EXIT:-0}"\n',
    )
    return rclone, log


def run_script(
    tmp_path: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    rclone, log = make_stub(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "BACKUP_REMOTE": "r2:test-bucket/backups",
            "RCLONE_BIN": str(rclone),
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


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_dry_run_prints_commands_without_calls(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--dry-run")

    assert result.returncode == 0
    assert "would check" in result.stderr
    assert "copyto" in result.stdout
    assert read(tmp_path / "rclone.log") == ""


def test_setup_runs_write_list_delete_probe(tmp_path: Path) -> None:
    result = run_script(tmp_path)

    assert result.returncode == 0
    assert "setup: ok" in result.stdout
    log = read(tmp_path / "rclone.log")
    assert "about" in log
    assert "copyto" in log
    assert "lsf" in log
    assert "deletefile" in log
    assert "mkdir" not in log


def test_init_creates_remote_prefix(tmp_path: Path) -> None:
    result = run_script(tmp_path, "--init")

    assert result.returncode == 0
    assert "mkdir" in read(tmp_path / "rclone.log")


def test_missing_remote_is_a_clear_error(tmp_path: Path) -> None:
    result = run_script(tmp_path, extra_env={"BACKUP_REMOTE": ""})

    assert result.returncode != 0
    assert "BACKUP_REMOTE is required" in result.stderr


def test_missing_rclone_is_a_clear_error(tmp_path: Path) -> None:
    result = run_script(
        tmp_path,
        extra_env={"RCLONE_BIN": str(tmp_path / "missing-rclone")},
    )

    assert result.returncode != 0
    assert "rclone is required" in result.stderr
