from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from app.services.reminders.quiet import (
    QuietHours,
    in_quiet_hours,
    next_allowed_utc,
    parse_clock,
    quiet_hours,
)


def test_in_quiet_hours_same_day_and_cross_midnight() -> None:
    same_day = QuietHours(start=time(13, 0), end=time(15, 0))
    assert in_quiet_hours(time(12, 59), same_day) is False
    assert in_quiet_hours(time(13, 0), same_day) is True
    assert in_quiet_hours(time(15, 0), same_day) is False

    overnight = QuietHours(start=time(22, 0), end=time(8, 0))
    assert in_quiet_hours(time(23, 0), overnight) is True
    assert in_quiet_hours(time(7, 59), overnight) is True
    assert in_quiet_hours(time(8, 0), overnight) is False
    assert in_quiet_hours(time(12, 0), overnight) is False


def test_parse_and_invalid_quiet_hours() -> None:
    assert parse_clock("08:30") == time(8, 30)
    assert parse_clock("nope") is None
    assert quiet_hours("22:00", "08:00") == QuietHours(time(22), time(8))
    assert quiet_hours("22:00", "22:00") is None
    assert quiet_hours(None, "08:00") is None


def test_next_allowed_utc_returns_none_outside_quiet() -> None:
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    hours = QuietHours(time(22), time(8))

    assert next_allowed_utc(now, "Europe/Moscow", hours) is None
    assert next_allowed_utc(now, "Nope/Invalid", hours) is None


def test_next_allowed_utc_uses_user_timezone_and_utc_storage() -> None:
    # 22:00 UTC = 01:00 MSK; quiet ends at 08:00 MSK = 05:00 UTC.
    now = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)
    hours = QuietHours(time(22), time(8))

    assert next_allowed_utc(now, "Europe/Moscow", hours) == datetime(2026, 10, 30, 5, 0, tzinfo=UTC)


def test_next_allowed_utc_handles_fall_back_ambiguity() -> None:
    # 01:00 EDT is the first pass through the repeated hour; quiet ends at the
    # second 01:30 (EST), i.e. 06:30 UTC.
    now = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
    hours = QuietHours(time(22), time(1, 30))

    assert next_allowed_utc(now, "America/New_York", hours) == datetime(
        2026, 11, 1, 6, 30, tzinfo=UTC
    )


def test_next_allowed_utc_handles_spring_forward_gap_safely() -> None:
    # 02:30 does not exist on 2026-03-08 in New York. The helper may return the
    # first safe instant after the gap (03:30 EDT = 07:30 UTC).
    now = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)
    hours = QuietHours(time(22), time(2, 30))

    result = next_allowed_utc(now, "America/New_York", hours)

    assert result == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    assert result is not None
    assert result.astimezone(ZoneInfo("America/New_York")).time() >= time(3, 0)
