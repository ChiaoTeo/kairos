"""Execution-owned business policy values."""

from __future__ import annotations

from dataclasses import dataclass
from kairospy.primitives.decimal import Money


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    allow_trading: bool
    max_order_notional: Money | None = None
    require_limit_orders: bool = False

    def __post_init__(self) -> None:
        if self.max_order_notional is not None:
            value = Money(self.max_order_notional)
            if value.value <= 0:
                raise ValueError("max_order_notional must be positive")
            object.__setattr__(self, "max_order_notional", value)
