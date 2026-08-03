from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from kairospy.domain.execution import ExecutionViewKeys
from kairospy.domain.order import OrderViewKeys
OrderViewSource = object


@dataclass(frozen=True, slots=True)
class OrderQueryService:
    """Semantic order queries; the backing view may be runtime-owned."""

    source: object

    def current(self) -> object:
        view = self.source.get(ExecutionViewKeys.current, None)
        if view is not None:
            return view
        order_view = self.source.get(OrderViewKeys.current, None)
        state = _field(order_view, "state", None)
        if state is not None:
            return state
        return self.source.require(ExecutionViewKeys.current)

    def orders(self) -> tuple[object, ...]:
        return tuple(_field(self.current(), "orders", ()) or ())

    def latest(self) -> object | None:
        return _field(self.current(), "latest_order", None)

    def order(self, order_id: str) -> object:
        wanted = str(order_id).strip()
        if not wanted:
            raise ValueError("order_id is required")
        for order in self.orders():
            if str(_field(order, "order_id", "")) == wanted:
                return order
        latest = self.latest()
        if latest is not None and str(_field(latest, "order_id", "")) == wanted:
            return latest
        raise KeyError(f"order was not found in execution view: {wanted}")

    def status(self, order_id: str) -> Mapping[str, object]:
        order = self.order(order_id)
        return MappingProxyType(
            {
                "order_id": _field(order, "order_id", order_id),
                "status": _field(order, "status", None),
                "order": order,
            }
        )


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


__all__ = ["OrderQueryService", "OrderViewSource"]
