from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol
from uuid import UUID

from kairospy.domain.account import (
    AccountEvent,
    AccountEventKind,
    AccountBookRef,
    AccountLedger,
    AccountContext,
    Environment,
)
from kairospy.domain.order import OrderJournal, OrderOrigin, OrderRequest, OrderSide, OrderState, OrderStatus, OrderType

from kairospy.domain.execution import Reservation, ReservationBook, ReservationStatus



class ExecutionStateOwner(Protocol):
    orders: OrderJournal
    ledger: AccountLedger
    reservations: ReservationBook


@dataclass(frozen=True, slots=True)
class ExecutionStateSnapshot:
    orders: tuple[OrderState, ...] = ()
    ledger_events: tuple[AccountEvent, ...] = ()
    reservations: tuple[Reservation, ...] = ()

    @classmethod
    def capture(cls, coordinator: ExecutionStateOwner) -> "ExecutionStateSnapshot":
        return cls(
            orders=coordinator.orders.states,
            ledger_events=coordinator.ledger.events,
            reservations=coordinator.reservations.reservations,
        )

    def restore_into(self, coordinator: ExecutionStateOwner) -> ExecutionStateOwner:
        coordinator.orders = OrderJournal.from_states(self.orders)
        restore_ledger = getattr(coordinator.ledger, "restore", None)
        if callable(restore_ledger):
            restore_ledger(self.ledger_events)
        else:
            coordinator.ledger = AccountLedger(self.ledger_events)
        restore_reservations = getattr(coordinator.reservations, "restore", None)
        if callable(restore_reservations):
            restore_reservations(self.reservations)
        else:
            coordinator.reservations = ReservationBook(self.reservations)
        return coordinator

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "orders": [_order_state_to_dict(order) for order in self.orders],
            "ledger_events": [_account_event_to_dict(event) for event in self.ledger_events],
            "reservations": [_reservation_to_dict(reservation) for reservation in self.reservations],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ExecutionStateSnapshot":
        return cls(
            orders=tuple(_order_state_from_dict(item) for item in _items(value.get("orders"))),
            ledger_events=tuple(_account_event_from_dict(item) for item in _items(value.get("ledger_events"))),
            reservations=tuple(_reservation_from_dict(item) for item in _items(value.get("reservations"))),
        )


def _order_state_to_dict(state: OrderState) -> dict[str, object]:
    request = state.request
    return {
        "request": {
            "order_id": request.order_id,
            "context": _account_context_to_dict(request.context),
            "instrument_id": str(request.instrument_id),
            "side": request.side.value,
            "quantity": str(request.quantity),
            "order_type": request.order_type.value,
            "limit_price": None if request.limit_price is None else str(request.limit_price),
            "market_id": None if request.market_id is None else str(request.market_id),
            "reservation_id": request.reservation_id,
            "origin": request.origin.value,
            "order_venue_id": request.order_venue_id,
        },
        "status": state.status.value,
        "order_venue_id": state.order_venue_id,
        "filled_quantity": str(state.filled_quantity),
        "updated_at": None if state.updated_at is None else state.updated_at.isoformat(),
        "reason": state.reason,
    }


def _order_state_from_dict(value: Mapping[str, object]) -> OrderState:
    request_value = _mapping(value.get("request"))
    request = OrderRequest(
        str(request_value["order_id"]),
        _account_context_from_dict(_mapping(request_value["context"])),
        str(request_value["instrument_id"]),
        OrderSide(str(request_value["side"])),
        Decimal(str(request_value["quantity"])),
        order_type=OrderType(str(request_value["order_type"])),
        limit_price=_optional_decimal(request_value.get("limit_price")),
        market_id=_optional_text(request_value.get("market_id")),
        reservation_id=_optional_text(request_value.get("reservation_id")),
        origin=OrderOrigin(str(request_value.get("origin") or OrderOrigin.SYSTEM.value)),
        order_venue_id=_optional_text(request_value.get("order_venue_id")),
    )
    return OrderState(
        request,
        status=OrderStatus(str(value["status"])),
        order_venue_id=_optional_text(value.get("order_venue_id")),
        filled_quantity=Decimal(str(value.get("filled_quantity") or "0")),
        updated_at=_optional_datetime(value.get("updated_at")),
        reason=str(value.get("reason") or ""),
    )


def _account_event_to_dict(event: AccountEvent) -> dict[str, object]:
    return {
        "event_id": str(event.event_id),
        "account": _account_book_to_dict(event.account),
        "kind": event.kind.value,
        "occurred_at": event.occurred_at.isoformat(),
        "currency": event.currency,
        "cash_delta": str(event.cash_delta),
        "instrument_id": None if event.instrument_id is None else str(event.instrument_id),
        "position_delta": str(event.position_delta),
        "reference_id": event.reference_id,
    }


def _account_event_from_dict(value: Mapping[str, object]) -> AccountEvent:
    return AccountEvent(
        UUID(str(value["event_id"])),
        _account_book_from_dict(_mapping(value["account"])),
        AccountEventKind(str(value["kind"])),
        _datetime(value["occurred_at"]),
        str(value["currency"]),
        cash_delta=Decimal(str(value.get("cash_delta") or "0")),
        instrument_id=_optional_text(value.get("instrument_id")),
        position_delta=Decimal(str(value.get("position_delta") or "0")),
        reference_id=str(value.get("reference_id") or ""),
    )


def _reservation_to_dict(reservation: Reservation) -> dict[str, object]:
    return {
        "reservation_id": reservation.reservation_id,
        "account": _account_book_to_dict(reservation.account),
        "currency": reservation.currency,
        "amount": str(reservation.amount),
        "reason": reservation.reason,
        "created_at": reservation.created_at.isoformat(),
        "order_id": reservation.order_id,
        "status": reservation.status.value,
    }


def _reservation_from_dict(value: Mapping[str, object]) -> Reservation:
    return Reservation(
        str(value["reservation_id"]),
        _account_book_from_dict(_mapping(value["account"])),
        str(value["currency"]),
        Decimal(str(value["amount"])),
        str(value["reason"]),
        _datetime(value["created_at"]),
        order_id=_optional_text(value.get("order_id")),
        status=ReservationStatus(str(value["status"])),
    )


def _account_context_to_dict(context: AccountContext) -> dict[str, object]:
    return {"account": _account_book_to_dict(context.book), "environment": context.environment.value}


def _account_context_from_dict(value: Mapping[str, object]) -> AccountContext:
    return AccountContext(_account_book_from_dict(_mapping(value["account"])), Environment(str(value["environment"])))


def _account_book_to_dict(account: AccountBookRef) -> dict[str, object]:
    return {
        "broker": str(account.broker),
        "account_id": str(account.account_id),
        "book": str(account.book),
        "qualifier": account.qualifier,
        "segment": account.segment,
    }


def _account_book_from_dict(value: Mapping[str, object]) -> AccountBookRef:
    return AccountBookRef(
        str(value["broker"]),
        str(value["account_id"]),
        str(value.get("book") or value.get("segment") or ""),
        str(value.get("qualifier") or ""),
    )


def _items(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(_mapping(item) for item in value)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("execution state item must be a mapping")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("execution state datetime must be timezone-aware")
    return parsed


__all__ = ["ExecutionStateOwner", "ExecutionStateSnapshot"]
