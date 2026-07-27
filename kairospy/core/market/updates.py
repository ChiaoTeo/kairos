from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class MarketUpdate:
    subject_type: str
    subject_id: str
    observed_at: datetime
    fields: Mapping[str, object]
    source: str = ""
    kind: str = "fields"
    available_at: datetime | None = None
    sequence: int | None = None
    market_id: str | None = None
    market_key: str | None = None
    interval: str | None = None
    metadata: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.subject_type.strip() or not self.subject_id.strip():
            raise ValueError("market update identity fields are required")
        _require_aware_time(self.observed_at, "market update observed_at")
        if self.available_at is not None:
            _require_aware_time(self.available_at, "market update available_at")
        if self.sequence is not None and self.sequence < 1:
            raise ValueError("market update sequence must be positive")
        if not self.kind.strip():
            raise ValueError("market update kind is required")
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def _require_aware_time(value: datetime, label: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = ["MarketUpdate"]
