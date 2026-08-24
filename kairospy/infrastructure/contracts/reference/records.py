"""Decoded Reference contract records, independent of Python applications."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReferenceEventRecord:
    event_id: str
    stream_id: str
    sequence: int
    producer: str
    kind: str
    catalog_revision: int
    occurred_at_unix_nanos: int
    payload: object
    launch_id: str | None = None
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.stream_id.strip():
            raise ValueError("Reference event identity is required")
        if self.sequence <= 0 or self.catalog_revision < 0:
            raise ValueError("Reference event watermark is invalid")


__all__ = ["ReferenceEventRecord"]
