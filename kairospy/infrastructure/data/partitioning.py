from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Mapping


TimePartitionGrain = Literal["none", "hour", "day", "month", "year"]


@dataclass(frozen=True, slots=True)
class PartitionSpec:
    time_grain: TimePartitionGrain = "none"
    path_fields: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_partitioned(self) -> bool:
        return self.time_grain != "none" or bool(self.path_fields)


def partition_path_parts(spec: PartitionSpec, time: datetime) -> tuple[str, ...]:
    parts = partition_field_path_parts(spec)
    if spec.time_grain == "none":
        return parts
    return (*parts, _time_partition_part(spec.time_grain, time))


def partition_field_path_parts(spec: PartitionSpec) -> tuple[str, ...]:
    return tuple(f"{_clean_key(key)}={_clean_value(value)}" for key, value in spec.path_fields.items())


def time_partition_parts_between(
    spec: PartitionSpec,
    *,
    start: datetime | None,
    end: datetime | None,
) -> tuple[str, ...]:
    if spec.time_grain == "none" or start is None or end is None:
        return ()
    if start >= end:
        return ()
    values: list[str] = []
    current = _floor_time(start, spec.time_grain)
    stop = _floor_time(end - timedelta(microseconds=1), spec.time_grain)
    while current <= stop:
        values.append(_time_partition_part(spec.time_grain, current))
        current = _advance_time(current, spec.time_grain)
    return tuple(values)


def is_partition_path_part(value: str) -> bool:
    return "=" in value


def _time_partition_part(grain: TimePartitionGrain, time: datetime) -> str:
    utc = time.astimezone(timezone.utc)
    if grain == "hour":
        return f"hour={utc:%Y-%m-%dT%H}"
    if grain == "day":
        return f"date={utc:%Y-%m-%d}"
    if grain == "month":
        return f"month={utc:%Y-%m}"
    if grain == "year":
        return f"year={utc:%Y}"
    raise ValueError(f"unsupported time partition grain: {grain}")


def _floor_time(time: datetime, grain: TimePartitionGrain) -> datetime:
    utc = time.astimezone(timezone.utc)
    if grain == "hour":
        return utc.replace(minute=0, second=0, microsecond=0)
    if grain == "day":
        return utc.replace(hour=0, minute=0, second=0, microsecond=0)
    if grain == "month":
        return utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if grain == "year":
        return utc.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return utc


def _advance_time(time: datetime, grain: TimePartitionGrain) -> datetime:
    if grain == "hour":
        return time + timedelta(hours=1)
    if grain == "day":
        return time + timedelta(days=1)
    if grain == "month":
        year = time.year + (1 if time.month == 12 else 0)
        month = 1 if time.month == 12 else time.month + 1
        return time.replace(year=year, month=month)
    if grain == "year":
        return time.replace(year=time.year + 1)
    return time


def _clean_key(value: object) -> str:
    text = str(value).strip().lower()
    if not text or any(character in text for character in "/\\="):
        raise ValueError(f"invalid partition key: {value!r}")
    return text


def _clean_value(value: object) -> str:
    text = str(value).strip().lower()
    if not text or any(character in text for character in "/\\"):
        raise ValueError(f"invalid partition value: {value!r}")
    return text.replace(":", "_")


__all__ = [
    "PartitionSpec",
    "TimePartitionGrain",
    "is_partition_path_part",
    "partition_field_path_parts",
    "partition_path_parts",
    "time_partition_parts_between",
]
