from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.durations import parse_duration


def test_parse_duration_units() -> None:
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("7d") == timedelta(days=7)
    assert parse_duration(" 1H ") == timedelta(hours=1)


@pytest.mark.parametrize("value", ["", "0m", "-1h", "10", "h", "1w", "abc"])
def test_parse_duration_rejects_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(value)
