from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "age_key.sh"


def write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def make_stub(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    keygen = bin_dir / "age-keygen"
    log = tmp_path / "age-keygen.log"
    write_executable(
        keygen,
        """if [ "$1" = "-o" ]; then
  printf 'AGE-SECRET-KEY-TEST\\n' > "$2"
fi
if [ "$1" = "-y" ]; then
  printf 'age1testpublic\\n'
fi
printf '%s\\n' "$*" >> "$AGE_KEYGEN_LOG"
""",
    )
    return keygen, log


def run_script(
    tmp_path: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    keygen, log = make_stub(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "AGE_KEY_FILE": str(tmp_path / "backups" / "age-key.txt"),
            "AGE_KEYGEN_BIN": str(keygen),
            "AGE_KEYGEN_LOG": str(log),
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


def test_generates_key_with_private_permissions(tmp_path: Path) -> None:
    key_file = tmp_path / "backups" / "age-key.txt"

    result = run_script(tmp_path)

    assert result.returncode == 0
    assert key_file.is_file()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert "age1testpublic" in result.stdout
    assert "AGE-SECRET-KEY-TEST" not in result.stdout


def test_refuses_to_overwrite_existing_key(tmp_path: Path) -> None:
    key_file = tmp_path / "backups" / "age-key.txt"
    key_file.parent.mkdir()
    key_file.write_text("old\n", encoding="utf-8")

    result = run_script(tmp_path)

    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr
    assert key_file.read_text(encoding="utf-8") == "old\n"


def test_force_overwrites_existing_key(tmp_path: Path) -> None:
    key_file = tmp_path / "backups" / "age-key.txt"
    key_file.parent.mkdir()
    key_file.write_text("old\n", encoding="utf-8")

    result = run_script(tmp_path, "--force")

    assert result.returncode == 0
    assert "AGE-SECRET-KEY-TEST" in key_file.read_text(encoding="utf-8")


def test_missing_age_keygen_is_a_clear_error(tmp_path: Path) -> None:
    result = run_script(
        tmp_path,
        extra_env={"AGE_KEYGEN_BIN": str(tmp_path / "missing-keygen")},
    )

    assert result.returncode != 0
    assert "age-keygen is required" in result.stderr
