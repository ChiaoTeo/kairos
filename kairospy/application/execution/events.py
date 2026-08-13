from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from kairospy.domain_types import DataEvent

from .models import ExecutionIntent, Fill, Order


@dataclass(frozen=True, slots=True)
class IntentUpdateEvent(DataEvent[ExecutionIntent]):
    kind: Literal["intent_update"] = field(init=False, default="intent_update")


@dataclass(frozen=True, slots=True)
class OrderUpdateEvent(DataEvent[Order]):
    kind: Literal["order_update"] = field(init=False, default="order_update")


@dataclass(frozen=True, slots=True)
class FillEvent(DataEvent[Fill]):
    kind: Literal["fill"] = field(init=False, default="fill")


ExecutionEvent: TypeAlias = IntentUpdateEvent | OrderUpdateEvent | FillEvent
