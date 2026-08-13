from __future__ import annotations

from dataclasses import dataclass

from datetime import datetime

from kairospy.strategy import CommandResult


@dataclass(frozen=True, slots=True)
class RawEventEnvelope:
    """Internal transport/runtime envelope; never exported to strategy authors."""

    stream_id: str
    sequence: int
    domain: str
    kind: str
    payload: object
    occurred_at: datetime | None = None
    schema_version: int = 1
    producer: str = ""
    causation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.stream_id.strip() or self.sequence <= 0:
            raise ValueError("raw event stream and positive sequence are required")
        if not self.domain.strip() or not self.kind.strip():
            raise ValueError("raw event domain and kind are required")


@dataclass(frozen=True, slots=True)
class StrategyCommand:
    """Typed command envelope passed from a strategy to composition."""

    strategy_id: str
    instance_id: str
    request_id: str
    operation: str
    payload: object

    def __post_init__(self) -> None:
        if not all(
            (
                self.strategy_id.strip(),
                self.instance_id.strip(),
                self.request_id.strip(),
                self.operation.strip(),
            )
        ):
            raise ValueError("strategy command identity and operation are required")


CommandHandle = CommandResult


@dataclass(frozen=True, slots=True)
class LifecycleRecord:
    launch_id: str
    instance_id: str
    strategy_id: str
    state: str
    reason: str | None = None
    event_sequence: int | None = None
    readiness: str | None = None
    data_health: str | None = None
