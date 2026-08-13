"""Execution-owned business policy values."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    allow_trading: bool
    max_order_notional: Decimal | None = None
    require_limit_orders: bool = False

    def __post_init__(self) -> None:
        if self.max_order_notional is not None and self.max_order_notional <= 0:
            raise ValueError("max_order_notional must be positive")
