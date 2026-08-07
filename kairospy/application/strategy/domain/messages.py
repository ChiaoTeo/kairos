from __future__ import annotations

from dataclasses import dataclass

from kairospy.strategy import (
    CommandResult,
    EventEnvelope,
    SubscriptionRequest,
    TargetPositionRequest,
)


@dataclass(frozen=True, slots=True)
class SnapshotEnvelope:
    view_key: str
    snapshot_id: str
    owner_actor_id: str
    event_stream_id: str
    event_sequence: int
    generation: int
    payload: object

    def __post_init__(self) -> None:
        if not all((self.view_key.strip(), self.snapshot_id.strip(), self.owner_actor_id.strip(), self.event_stream_id.strip())):
            raise ValueError("snapshot identity fields are required")
        if self.event_sequence < 0 or self.generation < 0:
            raise ValueError("snapshot sequence and generation cannot be negative")


MarketSubscriptionRequest = SubscriptionRequest


@dataclass(frozen=True, slots=True)
class StrategyCommand:
    """Typed command envelope passed from a strategy to composition."""

    strategy_id: str
    instance_id: str
    request_id: str
    operation: str
    payload: object

    def __post_init__(self) -> None:
        if not all((self.strategy_id.strip(), self.instance_id.strip(), self.request_id.strip(), self.operation.strip())):
            raise ValueError("strategy command identity and operation are required")


@dataclass(frozen=True, slots=True)
class IntentCommand:
    strategy_id: str
    intent: object


@dataclass(frozen=True, slots=True)
class ContextRequest:
    operation: str
    payload: object
    strategy_id: str
    request_id: str
    instance_id: str = ""


CommandHandle = CommandResult


@dataclass(frozen=True, slots=True)
class StrategySignal:
    strategy_id: str
    intent: object
    instance_id: str = ""
    source_stream_id: str | None = None
    source_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class LifecycleRecord:
    launch_id: str
    instance_id: str
    strategy_id: str
    state: str
    reason: str | None = None
    event_sequence: int | None = None
