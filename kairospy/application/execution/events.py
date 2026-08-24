from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from kairospy.application.events import DataEvent

from .models import ExecutionIntent, Fill, IntentStatus, Order


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


@dataclass(frozen=True, slots=True)
class IntentUpdateEvent(DataEvent[ExecutionIntent]):
    previous_status: "IntentStatus | None" = None
    kind: Literal["intent_update"] = field(init=False, default="intent_update")


@dataclass(frozen=True, slots=True)
class OrderUpdateEvent(DataEvent[Order]):
    kind: Literal["order_update"] = field(init=False, default="order_update")


@dataclass(frozen=True, slots=True)
class FillEvent(DataEvent[Fill]):
    kind: Literal["fill"] = field(init=False, default="fill")


ExecutionEvent: TypeAlias = IntentUpdateEvent | OrderUpdateEvent | FillEvent
