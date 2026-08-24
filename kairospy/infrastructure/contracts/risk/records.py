"""Decoded Risk contract records, independent of Python applications."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RiskEventRecord:
    stream_id: str
    sequence: int
    producer: str
    kind: str
    account_id: str | None
    strategy_id: str | None
    payload: object
    occurred_at_unix_nanos: int
    launch_id: str | None = None
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if not self.stream_id.strip() or self.sequence <= 0:
            raise ValueError("Risk event stream and positive sequence are required")


__all__ = ["RiskEventRecord"]
