from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.metrics import MetricsRegistry
from app.services.metrics import (
    metrics_loop,
    metrics_path,
    read_metrics_snapshot,
    write_metrics_snapshot,
)


def test_counters_and_timers_snapshot() -> None:
    metrics = MetricsRegistry()
    metrics.incr("updates_total")
    metrics.incr("updates_total", 2)
    metrics.observe("update_duration_ms", 10)
    metrics.observe("update_duration_ms", 30)

    snapshot = metrics.snapshot()

    assert snapshot["counters"] == {"updates_total": 3.0}
    timers = snapshot["timers"]
    assert isinstance(timers, dict)
    timer = timers["update_duration_ms"]
    assert timer["count"] == 2
    assert timer["avg_ms"] == 20.0
    assert timer["min_ms"] == 10.0
    assert timer["max_ms"] == 30.0


def test_negative_values_are_rejected() -> None:
    metrics = MetricsRegistry()

    with pytest.raises(ValueError):
        metrics.incr("bad", -1)
    with pytest.raises(ValueError):
        metrics.observe("bad", -1)


def test_snapshot_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    metrics = MetricsRegistry()
    metrics.incr("llm_calls_total")

    path = write_metrics_snapshot(db_path, metrics)
    loaded = read_metrics_snapshot(db_path)

    assert path == metrics_path(db_path)
    assert path.is_file()
    assert loaded is not None
    assert loaded["service"] == "bot"
    assert loaded["counters"] == {"llm_calls_total": 1.0}


def test_missing_snapshot_returns_none(tmp_path: Path) -> None:
    assert read_metrics_snapshot(tmp_path / "bot.db") is None


def test_invalid_snapshot_raises(tmp_path: Path) -> None:
    path = metrics_path(tmp_path / "bot.db")
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid metrics JSON"):
        read_metrics_snapshot(tmp_path / "bot.db")


async def test_metrics_loop_writes_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    metrics = MetricsRegistry()
    metrics.incr("updates_total")

    task = asyncio.create_task(metrics_loop(db_path, metrics, interval_seconds=1))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    loaded = read_metrics_snapshot(db_path)
    assert loaded is not None
    assert loaded["counters"] == {"updates_total": 1.0}
