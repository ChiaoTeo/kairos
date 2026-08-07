from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Stable event shape delivered to a strategy callback."""

    stream_id: str
    sequence: int
    domain: str
    kind: str
    payload: object
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.stream_id.strip():
            raise ValueError("event stream_id is required")
        if self.sequence <= 0:
            raise ValueError("event sequence must be positive")
        if not self.domain.strip() or not self.kind.strip():
            raise ValueError("event domain and kind are required")
