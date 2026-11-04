"""Small parsers for operator-supplied values."""

from __future__ import annotations

import re
from datetime import timedelta

_DURATION_RE = re.compile(r"^(\d+)([mhd])$")


def parse_duration(value: str) -> timedelta:
    """Parse durations such as ``30m``, ``24h`` or ``7d``."""
    match = _DURATION_RE.fullmatch(value.strip().lower())
    if match is None:
        raise ValueError(f"invalid duration {value!r}; use 30m, 24h or 7d")
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError("duration must be positive")
    unit = match.group(2)
    units = {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }
    return units[unit]
