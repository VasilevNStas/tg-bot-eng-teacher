"""Quiet-hours and DST-aware deferral helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class QuietHours:
    start: time
    end: time


def parse_clock(value: str) -> time | None:
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def quiet_hours(start: str | None, end: str | None) -> QuietHours | None:
    if not start or not end:
        return None
    start_time = parse_clock(start)
    end_time = parse_clock(end)
    if start_time is None or end_time is None or start_time == end_time:
        return None
    return QuietHours(start=start_time, end=end_time)


def in_quiet_hours(local_time: time, hours: QuietHours) -> bool:
    if hours.start < hours.end:
        return hours.start <= local_time < hours.end
    return local_time >= hours.start or local_time < hours.end


def next_allowed_utc(
    now: datetime,
    timezone: str,
    hours: QuietHours | None,
) -> datetime | None:
    """Return when a quiet-period reminder may be sent, or None if allowed now."""
    zone = _zone(timezone)
    local_now = now.astimezone(zone)
    if hours is None or not in_quiet_hours(local_now.time(), hours):
        return None

    candidates: list[datetime] = []
    for offset in range(3):
        day = local_now.date() + timedelta(days=offset)
        candidate = _wall_time_utc(day, hours.end, zone)
        if candidate <= now:
            continue
        if in_quiet_hours(candidate.astimezone(zone).time(), hours):
            continue
        candidates.append(candidate)
    return min(candidates) if candidates else now + timedelta(hours=1)


def _wall_time_utc(day: date, wall_time: time, zone: ZoneInfo) -> datetime:
    wall = datetime.combine(day, wall_time, tzinfo=zone)
    # On fall-back the wall time occurs twice: use the later UTC instant so we
    # never send inside the repeated hour. In a spring-forward gap the later
    # fold maps to the first valid wall time after the gap, which is also safe.
    first = wall.replace(fold=0).astimezone(UTC)
    second = wall.replace(fold=1).astimezone(UTC)
    return max(first, second)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")
