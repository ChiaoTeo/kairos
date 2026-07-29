from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from kairospy.core.account.model import AccountContext
from kairospy.core.reference import InstrumentId, MarketId

from .model import OrderEvent, OrderOrigin, OrderRequest, OrderSide, OrderState, OrderStatus, OrderType


@dataclass(frozen=True, slots=True)
class OrderRecord:
    state: OrderState
    events: tuple[OrderEvent, ...]


class OrderJournal:
    def __init__(self) -> None:
        self._states: dict[str, OrderState] = {}
        self._events: dict[str, list[OrderEvent]] = {}
        self._order_venue_index: dict[str, str] = {}

    def plan(self, request: OrderRequest) -> OrderState:
        if request.origin is not OrderOrigin.SYSTEM:
            raise ValueError("external orders must be imported, not planned")
        if request.order_id in self._states:
            raise ValueError(f"duplicate order: {request.order_id}")
        state = OrderState(request)
        self._put_state(state)
        self._events[request.order_id] = []
        return state

    def import_open_order(
        self,
        request: OrderRequest,
        *,
        status: OrderStatus = OrderStatus.ACKNOWLEDGED,
        filled_quantity: Decimal = Decimal("0"),
        observed_at: datetime | None = None,
    ) -> OrderState:
        if request.origin is OrderOrigin.SYSTEM:
            raise ValueError("system orders must be planned, not imported")
        if status in {OrderStatus.PLANNED, OrderStatus.RESERVED, OrderStatus.SUBMITTING}:
            raise ValueError("external orders cannot import local pre-submit states")
        if status.terminal:
            raise ValueError("import_open_order only accepts active venue orders")
        if request.order_id in self._states:
            existing = self._states[request.order_id]
            if existing.request != request:
                raise ValueError(f"conflicting imported order: {request.order_id}")
            return existing
        state = OrderState(
            request,
            status=status,
            order_venue_id=request.order_venue_id,
            filled_quantity=filled_quantity,
            updated_at=observed_at,
        )
        self._put_state(state)
        self._events[request.order_id] = []
        return state

    def import_order_venue_open_order(
        self,
        *,
        context: AccountContext,
        order_venue_id: str,
        instrument_id: InstrumentId | str,
        side: OrderSide | str,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Decimal | None = None,
        status: OrderStatus = OrderStatus.ACKNOWLEDGED,
        filled_quantity: Decimal = Decimal("0"),
        observed_at: datetime | None = None,
        origin: OrderOrigin = OrderOrigin.VENUE,
        market_id: MarketId | str | None = None,
    ) -> OrderState:
        request = OrderRequest.external(
            context=context,
            order_venue_id=order_venue_id,
            instrument_id=instrument_id,
            market_id=market_id,
            side=side if isinstance(side, OrderSide) else OrderSide(side),
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            origin=origin,
        )
        return self.import_open_order(
            request,
            status=status,
            filled_quantity=filled_quantity,
            observed_at=observed_at,
        )

    def record(self, event: OrderEvent) -> OrderState:
        order_id = self._resolve_order_id(event.order_id, event.order_venue_id)
        state = self._states.get(order_id)
        if state is None:
            raise KeyError(event.order_id)
        if event.order_id != state.request.order_id and (
            event.order_venue_id == state.identity.order_venue_id
            or event.order_id == state.identity.order_venue_id
        ):
            event = replace(event, order_id=state.request.order_id)
        updated = state.apply(event)
        self._put_state(updated)
        self._events[order_id].append(event)
        return updated

    def get(self, order_id: str) -> OrderState:
        resolved_order_id = self._resolve_order_id(order_id, None)
        try:
            return self._states[resolved_order_id]
        except KeyError as error:
            raise LookupError(order_id) from error

    def get_by_order_venue_id(self, order_venue_id: str) -> OrderState:
        try:
            return self._states[self._order_venue_index[order_venue_id]]
        except KeyError as error:
            raise LookupError(order_venue_id) from error

    def record_for(self, order_id: str) -> OrderRecord:
        resolved_order_id = self._resolve_order_id(order_id, None)
        return OrderRecord(self.get(order_id), tuple(self._events.get(resolved_order_id, ())))

    def active_for_context(self, context: AccountContext) -> tuple[OrderState, ...]:
        return tuple(
            state
            for state in self._states.values()
            if state.request.context == context and not state.status.terminal
        )

    @property
    def states(self) -> tuple[OrderState, ...]:
        return tuple(self._states.values())

    @classmethod
    def from_states(cls, states: tuple[OrderState, ...]) -> "OrderJournal":
        journal = cls()
        for state in states:
            journal._put_state(state)
            journal._events[state.order_id] = []
        return journal

    def _put_state(self, state: OrderState) -> None:
        order_id = state.order_id
        self._states[order_id] = state
        identity = state.identity
        if identity.order_venue_id is not None:
            self._order_venue_index[identity.order_venue_id] = order_id

    def _resolve_order_id(self, order_id: str, order_venue_id: str | None) -> str:
        if order_id in self._states:
            return order_id
        if order_id in self._order_venue_index:
            return self._order_venue_index[order_id]
        if order_venue_id is not None and order_venue_id in self._order_venue_index:
            return self._order_venue_index[order_venue_id]
        return order_id


__all__ = ["OrderJournal", "OrderRecord"]
