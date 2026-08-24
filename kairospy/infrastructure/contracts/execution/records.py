"""Decoded Execution contract records, independent of Python applications."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionChangeRecord:
    kind: str
    strategy_id: str
    account_id: str | None
    payload: object


@dataclass(frozen=True, slots=True)
class ExecutionEventRecord:
    stream_id: str
    sequence: int
    producer: str
    instance_id: str | None
    changes: tuple[ExecutionChangeRecord, ...]
    occurred_at_unix_nanos: int
    launch_id: str | None = None

    def __post_init__(self) -> None:
        if not self.stream_id.strip() or self.sequence <= 0:
            raise ValueError(
                "Execution event stream and positive sequence are required"
            )


__all__ = ["ExecutionChangeRecord", "ExecutionEventRecord"]
