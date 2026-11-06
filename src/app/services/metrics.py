"""Persist process metrics to the shared data volume for CLI/admin readers."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from app.core.logging import get_logger
from app.core.metrics import MetricsRegistry

logger = get_logger(__name__)

METRICS_FILENAME = "metrics.json"


def metrics_path(db_path: Path) -> Path:
    """Metrics live next to the heartbeat inside the shared data volume."""
    return db_path.parent / "health" / METRICS_FILENAME


def write_metrics_snapshot(
    db_path: Path,
    metrics: MetricsRegistry,
    *,
    service: str = "bot",
) -> Path:
    """Atomically write one JSON snapshot."""
    path = metrics_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "service": service,
        "generated_at": datetime.now(UTC).isoformat(),
        **metrics.snapshot(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def read_metrics_snapshot(db_path: Path) -> dict[str, object] | None:
    """Read a snapshot; ``None`` means the bot has not written one yet."""
    path = metrics_path(db_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid metrics JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: metrics snapshot must be a JSON object")
    return data


async def metrics_loop(
    db_path: Path,
    metrics: MetricsRegistry,
    *,
    interval_seconds: float = 60.0,
    service: str = "bot",
) -> None:
    """Write snapshots until cancelled, surviving individual failures."""
    if interval_seconds < 1:
        raise ValueError("interval_seconds must be positive")
    logger.info("metrics_loop_started", interval_seconds=interval_seconds)
    try:
        while True:
            try:
                await asyncio.to_thread(
                    write_metrics_snapshot,
                    db_path,
                    metrics,
                    service=service,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("metrics_snapshot_failed")
            await asyncio.sleep(interval_seconds)
    finally:
        logger.info("metrics_loop_stopped")
