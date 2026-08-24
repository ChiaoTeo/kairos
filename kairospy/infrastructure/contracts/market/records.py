"""Decoded Market contract records, independent of Python applications."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MarketEventRecord:
    stream_id: str
    sequence: int
    kind: str
    payload: object
    occurred_at: datetime | None = None
    schema_version: int = 1
    producer: str = "market"
    causation_id: str | None = None
    launch_id: str | None = None
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if not self.stream_id.strip() or self.sequence <= 0:
            raise ValueError("Market event stream and positive sequence are required")
        if not self.kind.strip():
            raise ValueError("Market event kind is required")


__all__ = ["MarketEventRecord"]
