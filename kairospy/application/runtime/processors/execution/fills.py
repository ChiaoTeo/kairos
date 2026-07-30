from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.views import ViewFieldSchema, ViewSchema


@dataclass(frozen=True, slots=True)
class ExecutionFillSummary:
    order_id: str
    intent_id: str | None
    instrument_id: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    occurred_at: datetime
    notional: Decimal | None = None
    market_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionFillsView:
    total_fills: int = 0
    fills: tuple[ExecutionFillSummary, ...] = ()


class ExecutionFillsViewState:
    key = "execution.fills"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("total_fills", "known execution fill count", "runtime state", "execution service"),
            ViewFieldSchema("fills", "execution fill summaries", "fill event time", "execution service"),
        ),
        mutability="runtime_writable",
        evidence="runtime execution fill view state",
    )

    def __init__(self, coordinator: ExecutionCoordinator, fills_source: object | None = None) -> None:
        self.coordinator = coordinator
        self.fills_source = fills_source

    def view(self) -> ExecutionFillsView:
        fills = tuple(_fill_summary(fill, market_id=self._market_id(fill)) for fill in _fills(self.fills_source))
        return ExecutionFillsView(total_fills=len(fills), fills=fills)

    def _market_id(self, fill: object) -> str | None:
        order_id = str(getattr(fill, "order_id", "") or "")
        if not order_id:
            return None
        for order in self.coordinator.orders.states:
            if order.order_id == order_id:
                return _optional_text(order.request.market_id)
        return None


def _fills(source: object | None) -> tuple[object, ...]:
    value = getattr(source, "fills", ()) if source is not None else ()
    return tuple(value or ())


def _fill_summary(fill: object, *, market_id: str | None) -> ExecutionFillSummary:
    side = getattr(fill, "side", "")
    return ExecutionFillSummary(
        order_id=str(getattr(fill, "order_id", "") or ""),
        intent_id=_optional_text(getattr(fill, "intent_id", None)),
        instrument_id=str(getattr(fill, "instrument_id", "") or ""),
        side=str(getattr(side, "value", side) or ""),
        quantity=getattr(fill, "quantity"),
        price=getattr(fill, "price"),
        fee=getattr(fill, "fee"),
        occurred_at=getattr(fill, "occurred_at"),
        notional=getattr(fill, "notional", None),
        market_id=market_id,
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


__all__ = ["ExecutionFillsViewState", "ExecutionFillsView", "ExecutionFillSummary"]
