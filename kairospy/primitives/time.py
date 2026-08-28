from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import NewType, Self


_NANOS_PER_SECOND = 1_000_000_000


class _UnsignedSemanticInt(int):
    """Validated construction type for an unsigned semantic integer."""

    def __new__(cls, value: int) -> Self:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{cls.__name__} must be constructed from an integer")
        if value < 0:
            raise ValueError(f"{cls.__name__} cannot be negative")
        if value > 2**64 - 1:
            raise ValueError(f"{cls.__name__} exceeds the u64 range")
        return int.__new__(cls, value)


class UnixNanos(_UnsignedSemanticInt):
    """Nanoseconds since the Unix epoch."""


class Sequence(_UnsignedSemanticInt):
    """Monotonic owner or stream sequence."""


class Generation(_UnsignedSemanticInt):
    """Immutable-view generation."""


class DurationNanos(_UnsignedSemanticInt):
    """Non-negative duration in nanoseconds."""


class BasisPoints(_UnsignedSemanticInt):
    """Non-negative basis-point count."""


# Native companions return built-in ``int`` instances. These nominal read
# types preserve semantic distinctions for static consumers without requiring
# a second Python object to be materialized.
UnixNanosRead = NewType("UnixNanosRead", int)
SequenceRead = NewType("SequenceRead", int)
GenerationRead = NewType("GenerationRead", int)
DurationNanosRead = NewType("DurationNanosRead", int)
BasisPointsRead = NewType("BasisPointsRead", int)


def datetime_from_unix_nanos(value: int | UnixNanos) -> datetime:
    if isinstance(value, bool) or value < 0:
        raise ValueError("unix nanos must be a non-negative integer")
    seconds, nanos = divmod(value, _NANOS_PER_SECOND)
    return datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(
        microseconds=nanos // 1_000
    )


def unix_nanos_from_datetime(value: datetime) -> UnixNanos:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - epoch
    if delta < timedelta(0):
        raise ValueError("datetime cannot be before the Unix epoch")
    return UnixNanos(
        delta.days * 86_400 * _NANOS_PER_SECOND
        + delta.seconds * _NANOS_PER_SECOND
        + delta.microseconds * 1_000
    )


__all__ = [
    "BasisPoints",
    "BasisPointsRead",
    "DurationNanos",
    "DurationNanosRead",
    "Generation",
    "GenerationRead",
    "Sequence",
    "SequenceRead",
    "UnixNanos",
    "UnixNanosRead",
    "datetime_from_unix_nanos",
    "unix_nanos_from_datetime",
]
