from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapitalEventRecord:
    stream_id: str
    sequence: int
    producer: str
    kind: str
    payload: object
    occurred_at_unix_nanos: int
    launch_id: str | None = None
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if not self.stream_id.strip() or self.sequence <= 0:
            raise ValueError("Capital event stream and positive sequence are required")


__all__ = ["CapitalEventRecord"]
