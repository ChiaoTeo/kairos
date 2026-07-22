from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..contracts import SourceBinding


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("time range requires timezone-aware [start,end) with start before end")


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    logical_key: str
    missing: tuple[TimeRange, ...]
    source: SourceBinding
    instruments: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    base_release_id: str | None = None


@dataclass(frozen=True, slots=True)
class AcquisitionEstimate:
    requests: int
    bytes: int | None = None
    cost_class: str = "unknown"
    instruments: int | None = None

    def __post_init__(self) -> None:
        if (
            self.requests < 0
            or self.bytes is not None and self.bytes < 0
            or self.instruments is not None and self.instruments < 0
        ):
            raise ValueError("acquisition estimates cannot be negative")


@dataclass(frozen=True, slots=True)
class AcquisitionLimits:
    maximum_requests: int = 10_000
    maximum_ranges: int = 366
    maximum_instruments: int = 10_000
    maximum_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class AcquisitionPlan:
    logical_key: str
    requested: TimeRange
    local_release_id: str | None
    covered: tuple[TimeRange, ...]
    missing: tuple[TimeRange, ...]
    candidates: tuple[SourceBinding, ...]
    selected: SourceBinding | None
    source_policy_version: str = "priority-v1"
    connector_available: bool = False
    estimate: AcquisitionEstimate | None = None

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def executable(self) -> bool:
        return self.complete or self.selected is not None and self.connector_available
