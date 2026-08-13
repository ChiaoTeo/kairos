from __future__ import annotations

from datetime import datetime, timedelta, timezone


_NANOS_PER_SECOND = 1_000_000_000


def datetime_from_unix_nanos(value: int) -> datetime:
    if value < 0:
        raise ValueError("unix nanos cannot be negative")
    seconds, nanos = divmod(value, _NANOS_PER_SECOND)
    return datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(
        microseconds=nanos // 1_000
    )


def unix_nanos_from_datetime(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    seconds = int(utc.timestamp())
    return seconds * _NANOS_PER_SECOND + utc.microsecond * 1_000
