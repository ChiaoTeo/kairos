from __future__ import annotations

from datetime import datetime, timedelta, timezone


_NANOS_PER_SECOND = 1_000_000_000


def datetime_from_unix_nanos(value: int) -> datetime:
    if isinstance(value, bool) or value < 0:
        raise ValueError("unix nanos must be a non-negative integer")
    seconds, nanos = divmod(value, _NANOS_PER_SECOND)
    return datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(
        microseconds=nanos // 1_000
    )


def unix_nanos_from_datetime(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - epoch
    if delta < timedelta(0):
        raise ValueError("datetime cannot be before the Unix epoch")
    return (
        delta.days * 86_400 * _NANOS_PER_SECOND
        + delta.seconds * _NANOS_PER_SECOND
        + delta.microseconds * 1_000
    )


__all__ = ["datetime_from_unix_nanos", "unix_nanos_from_datetime"]
