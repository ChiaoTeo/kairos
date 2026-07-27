from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from kairospy.core.account.model import AccountContext

from .model import OrderEvent, OrderOrigin, OrderRequest, OrderSide, OrderState, OrderStatus, OrderType


@dataclass(frozen=True, slots=True)
class OrderRecord:
    state: OrderState
    events: tuple[OrderEvent, ...]


class OrderJournal:
    def __init__(self) -> None:
        self._states: dict[str, OrderState] = {}
        self._events: dict[str, list[OrderEvent]] = {}
        self._client_index: dict[str, str] = {}
        self._venue_index: dict[str, str] = {}

    def plan(self, request: OrderRequest) -> OrderState:
        if request.origin is not OrderOrigin.SYSTEM:
            raise ValueError("external orders must be imported, not planned")
        if request.local_order_id in self._states:
            raise ValueError(f"duplicate order: {request.local_order_id}")
        state = OrderState(request)
        self._put_state(state)
        self._events[request.local_order_id] = []
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
        if request.local_order_id in self._states:
            existing = self._states[request.local_order_id]
            if existing.request != request:
                raise ValueError(f"conflicting imported order: {request.local_order_id}")
            return existing
        state = OrderState(
            request,
            status=status,
            venue_order_id=request.venue_order_id,
            filled_quantity=filled_quantity,
            updated_at=observed_at,
        )
        self._put_state(state)
        self._events[request.local_order_id] = []
        return state

    def import_venue_open_order(
        self,
        *,
        context: AccountContext,
        venue_order_id: str,
        instrument_id: str,
        side: OrderSide | str,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Decimal | None = None,
        status: OrderStatus = OrderStatus.ACKNOWLEDGED,
        filled_quantity: Decimal = Decimal("0"),
        observed_at: datetime | None = None,
        origin: OrderOrigin = OrderOrigin.VENUE,
        market_id: str | None = None,
    ) -> OrderState:
        request = OrderRequest.external(
            context=context,
            venue_order_id=venue_order_id,
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
        order_id = self._resolve_order_id(event.client_order_id, event.venue_order_id)
        state = self._states.get(order_id)
        if state is None:
            raise KeyError(event.client_order_id)
        if event.client_order_id != state.request.client_order_id and (
            event.venue_order_id == state.identity.venue_order_id
            or event.client_order_id == state.identity.venue_order_id
        ):
            event = replace(event, client_order_id=state.request.client_order_id)
        updated = state.apply(event)
        self._put_state(updated)
        self._events[order_id].append(event)
        return updated

    def get(self, client_order_id: str) -> OrderState:
        order_id = self._resolve_order_id(client_order_id, None)
        try:
            return self._states[order_id]
        except KeyError as error:
            raise LookupError(client_order_id) from error

    def get_by_venue_order_id(self, venue_order_id: str) -> OrderState:
        try:
            return self._states[self._venue_index[venue_order_id]]
        except KeyError as error:
            raise LookupError(venue_order_id) from error

    def record_for(self, client_order_id: str) -> OrderRecord:
        order_id = self._resolve_order_id(client_order_id, None)
        return OrderRecord(self.get(client_order_id), tuple(self._events.get(order_id, ())))

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
            journal._events[state.local_order_id] = []
        return journal

    def _put_state(self, state: OrderState) -> None:
        order_id = state.local_order_id
        self._states[order_id] = state
        identity = state.identity
        if identity.client_order_id is not None:
            self._client_index[identity.client_order_id] = order_id
        if identity.venue_order_id is not None:
            self._venue_index[identity.venue_order_id] = order_id

    def _resolve_order_id(self, order_id: str, venue_order_id: str | None) -> str:
        if order_id in self._states:
            return order_id
        if order_id in self._client_index:
            return self._client_index[order_id]
        if order_id in self._venue_index:
            return self._venue_index[order_id]
        if venue_order_id is not None and venue_order_id in self._venue_index:
            return self._venue_index[venue_order_id]
        return order_id


__all__ = ["OrderJournal", "OrderRecord"]
