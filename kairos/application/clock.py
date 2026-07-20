from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(slots=True)
class FixedClock:
    current: datetime

    def __post_init__(self) -> None:
        self._validate(self.current)

    def now(self) -> datetime:
        return self.current

    def set(self, value: datetime) -> None:
        self._validate(value)
        self.current = value

    @staticmethod
    def _validate(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock timestamps must be timezone-aware")
