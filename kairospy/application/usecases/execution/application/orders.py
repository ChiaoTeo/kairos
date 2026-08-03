from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from kairospy.domain.account import AccountBookRef
from kairospy.domain.order import OrderSide, OrderType


@dataclass(frozen=True, slots=True)
class ExecutionOrderOptions:
    """Typed order options accepted by the execution application."""

    time_in_force: str | None = None
    reduce_only: bool | None = None
    post_only: bool | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, object] | None) -> "ExecutionOrderOptions | None":
        if not values:
            return None

        def _text(*keys: str) -> str | None:
            for key in keys:
                value = values.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
            return None

        def _bool(*keys: str) -> bool | None:
            for key in keys:
                if key not in values or values[key] is None:
                    continue
                value = values[key]
                if isinstance(value, bool):
                    return value
                normalized = str(value).strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off"}:
                    return False
                raise ValueError(f"invalid boolean execution option: {value!r}")
            return None

        result = cls(
            time_in_force=_text("time_in_force", "timeInForce"),
            reduce_only=_bool("reduce_only", "reduceOnly"),
            post_only=_bool("post_only", "postOnly"),
        )
        return None if result == cls() else result


@dataclass(frozen=True, slots=True)
class OrderSubmissionRequest:
    account: AccountBookRef
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    options: ExecutionOrderOptions | None = None


@dataclass(frozen=True, slots=True)
class OrderSubmissionResult:
    order_venue_id: str
    status: str = ""


@dataclass(frozen=True, slots=True)
class OrderCancelRequest:
    account: AccountBookRef
    order_venue_id: str
    symbol: str | None = None
    options: ExecutionOrderOptions | None = None


@dataclass(frozen=True, slots=True)
class OrderCancelResult:
    order_venue_id: str
    status: str = ""


__all__ = [
    "ExecutionOrderOptions",
    "OrderCancelRequest",
    "OrderCancelResult",
    "OrderSubmissionRequest",
    "OrderSubmissionResult",
]
