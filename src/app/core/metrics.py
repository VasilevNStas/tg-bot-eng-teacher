"""Process-local counters and timers for lightweight observability.

The registry is intentionally dependency-free: the bot writes a JSON snapshot
to the shared data volume, and ``app.cli metrics`` reads it from another
process. Prometheus/APM can be added later without changing call sites.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(slots=True)
class TimerSnapshot:
    """Aggregated observations for one timer."""

    count: int = 0
    total_ms: float = 0.0
    min_ms: float | None = None
    max_ms: float | None = None

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count else 0.0

    def observe(self, value_ms: float) -> None:
        self.count += 1
        self.total_ms += value_ms
        self.min_ms = value_ms if self.min_ms is None else min(self.min_ms, value_ms)
        self.max_ms = value_ms if self.max_ms is None else max(self.max_ms, value_ms)

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "count": self.count,
            "total_ms": round(self.total_ms, 3),
            "avg_ms": round(self.avg_ms, 3),
            "min_ms": None if self.min_ms is None else round(self.min_ms, 3),
            "max_ms": None if self.max_ms is None else round(self.max_ms, 3),
        }


class MetricsRegistry:
    """In-memory counters and timers for one process."""

    def __init__(self) -> None:
        self._started_at = datetime.now(UTC)
        self._started_monotonic = time.monotonic()
        self._counters: dict[str, float] = {}
        self._timers: dict[str, TimerSnapshot] = {}

    def incr(self, name: str, value: float = 1.0) -> None:
        """Increment a counter (tokens, costs and event counts)."""
        if value < 0:
            raise ValueError("counter increment must not be negative")
        self._counters[name] = self._counters.get(name, 0.0) + value

    def observe(self, name: str, value_ms: float) -> None:
        """Record one duration observation in milliseconds."""
        if value_ms < 0:
            raise ValueError("timer observation must not be negative")
        self._timers.setdefault(name, TimerSnapshot()).observe(value_ms)

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot for the shared volume."""
        uptime = max(0.0, time.monotonic() - self._started_monotonic)
        return {
            "started_at": self._started_at.isoformat(),
            "uptime_seconds": round(uptime, 3),
            "counters": {name: round(value, 6) for name, value in sorted(self._counters.items())},
            "timers": {name: timer.as_dict() for name, timer in sorted(self._timers.items())},
        }

    def reset(self) -> None:
        """Clear counters and timers (tests only)."""
        self._counters.clear()
        self._timers.clear()
