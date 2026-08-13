from __future__ import annotations

from dataclasses import dataclass

from kairospy.strategy import CommandResult


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
    dispatch_sequence: int | None = None
    readiness: str | None = None
    data_health: str | None = None
