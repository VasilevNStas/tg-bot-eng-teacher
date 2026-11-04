"""Process and data health checks shared by the CLI and the admin bot.

The bot uses long polling, so there is no inbound port to probe. Liveness is
tracked with a heartbeat file written by each bot process; readiness also
checks the database, the Alembic revision and the seeded content.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text

from app.db.database import Database
from app.db.models import ContentItem, ProcessedUpdate

_MIN_FREE_MB = 50
_HEARTBEAT_MAX_AGE = timedelta(seconds=120)


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """Result of one independent probe."""

    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Aggregated health snapshot."""

    service: str
    checks: tuple[HealthCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks if check.required)

    def as_dict(self) -> dict[str, object]:
        return {
            "service": self.service,
            "ok": self.ok,
            "checks": [asdict(check) for check in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2)


def heartbeat_path(db_path: Path, service: str) -> Path:
    """Heartbeat files live next to the database, inside the shared volume."""
    return db_path.parent / "health" / f"{service}.heartbeat"


def write_heartbeat(db_path: Path, service: str) -> None:
    """Touch the heartbeat file with the current UTC timestamp."""
    path = heartbeat_path(db_path, service)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")


async def heartbeat_loop(
    db_path: Path,
    service: str,
    *,
    interval_seconds: float = 30.0,
) -> None:
    """Keep the heartbeat fresh for the lifetime of the process."""
    while True:
        write_heartbeat(db_path, service)
        await asyncio.sleep(interval_seconds)


async def check_health(
    db_path: Path,
    service: str,
    *,
    now: datetime | None = None,
) -> HealthReport:
    """Run every probe and return a report; never raises for a failed check."""
    moment = now or datetime.now(UTC)
    checks: list[HealthCheck] = []

    if not db_path.exists():  # noqa: ASYNC240 - one local FS probe
        checks.append(HealthCheck("database", False, f"missing: {db_path}"))
        checks.append(HealthCheck("migration", False, "database is missing"))
        checks.append(HealthCheck("content", False, "database is missing"))
    else:
        checks.extend(await _database_checks(db_path))

    checks.append(_heartbeat_check(db_path, service, moment))
    checks.append(_disk_check(db_path))
    return HealthReport(service=service, checks=tuple(checks))


async def _database_checks(db_path: Path) -> list[HealthCheck]:
    """Open the database once and probe connectivity, schema and content."""
    database = Database()
    database.init(db_path)
    try:
        async with database.session() as session:
            await session.execute(text("SELECT 1"))
            version = await session.scalar(text("SELECT version_num FROM alembic_version"))
            content = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ContentItem)
                    .where(ContentItem.is_active.is_(True))
                )
                or 0
            )
            last_update = await session.scalar(select(func.max(ProcessedUpdate.processed_at)))
    except Exception as exc:
        return [
            HealthCheck("database", False, f"error: {exc}"),
            HealthCheck("migration", False, "database unavailable"),
            HealthCheck("content", False, "database unavailable"),
        ]
    finally:
        await database.dispose()

    checks = [HealthCheck("database", True, "connected")]

    head = _head_revision()
    if head is None:
        checks.append(HealthCheck("migration", False, "alembic head not found"))
    elif version != head:
        checks.append(HealthCheck("migration", False, f"db={version or 'none'} head={head}"))
    else:
        checks.append(HealthCheck("migration", True, head))

    if content > 0:
        checks.append(HealthCheck("content", True, f"{content} active items"))
    else:
        checks.append(HealthCheck("content", False, "no active content"))

    if last_update is not None:
        checks.append(
            HealthCheck("updates", True, f"last update {last_update.isoformat()}", required=False)
        )
    return checks


def _head_revision() -> str | None:
    """Read the Alembic head from the bundled migrations."""
    try:
        script = ScriptDirectory.from_config(Config("alembic.ini"))
    except Exception:
        return None
    return script.get_current_head()


def _heartbeat_check(db_path: Path, service: str, now: datetime) -> HealthCheck:
    path = heartbeat_path(db_path, service)
    if not path.exists():
        return HealthCheck("heartbeat", False, f"missing: {path}")
    try:
        stamp = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return HealthCheck("heartbeat", False, "unreadable timestamp")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    age = now - stamp
    if age > _HEARTBEAT_MAX_AGE:
        return HealthCheck("heartbeat", False, f"stale: {age.total_seconds():.0f}s")
    return HealthCheck("heartbeat", True, f"age {age.total_seconds():.0f}s")


def _disk_check(db_path: Path) -> HealthCheck:
    usage = shutil.disk_usage(db_path.parent)
    free_mb = usage.free // (1024 * 1024)
    ok = free_mb >= _MIN_FREE_MB
    return HealthCheck("disk", ok, f"{free_mb} MB free")


def format_health(report: HealthReport) -> str:
    """Render a compact human-readable report for logs and Telegram."""
    lines = [f"{'OK' if report.ok else 'FAIL'} {report.service}"]
    for check in report.checks:
        mark = "ok" if check.ok else "FAIL"
        suffix = "" if check.required else " (info)"
        lines.append(f"  [{mark}] {check.name}: {check.detail}{suffix}")
    return "\n".join(lines)
