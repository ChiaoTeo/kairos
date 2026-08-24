from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar


TData_co = TypeVar("TData_co", covariant=True)


@dataclass(frozen=True, slots=True)
class EventMetadata:
    """Application event context mapped from an owning module's contract."""

    stream_id: str
    sequence: int
    schema_version: int = 1
    producer: str = ""
    occurred_at: datetime | None = None
    occurred_at_unix_nanos: int | None = None
    causation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.stream_id.strip():
            raise ValueError("event stream_id is required")
        if self.sequence <= 0:
            raise ValueError("event sequence must be positive")
        if self.schema_version <= 0:
            raise ValueError("event schema_version must be positive")


@dataclass(frozen=True, slots=True)
class DataEvent(Generic[TData_co]):
    """Base shape used by module-owned application events."""

    data: TData_co
    metadata: EventMetadata


__all__ = ["DataEvent", "EventMetadata"]
