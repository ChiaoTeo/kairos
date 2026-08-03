from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.domain.order import OrderRequest, OrderType


@dataclass(frozen=True, slots=True)
class ExecutionSafetyPolicy:
    trading_enabled: bool = False
    require_limit_orders: bool = True
    max_order_notional: Decimal | str | None = None

    def __post_init__(self) -> None:
        if self.max_order_notional is None:
            return
        value = Decimal(str(self.max_order_notional))
        if value <= 0:
            raise ValueError("max_order_notional must be positive")
        object.__setattr__(self, "max_order_notional", value)

    def reject_reason(self, request: OrderRequest) -> str:
        if not self.trading_enabled:
            return "live trading is disabled"
        if self.require_limit_orders and request.order_type is not OrderType.LIMIT:
            return "live trading requires limit orders"
        if self.max_order_notional is None:
            return ""
        if request.limit_price is None:
            return "live max_order_notional requires a limit price"
        notional = request.quantity * request.limit_price
        if notional > self.max_order_notional:
            return f"order notional {notional} exceeds max_order_notional {self.max_order_notional}"
        return ""


__all__ = ["ExecutionSafetyPolicy"]
