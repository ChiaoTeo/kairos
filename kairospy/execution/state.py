from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Mapping
from uuid import UUID

from kairospy.accounts import (
    AccountEvent,
    AccountEventKind,
    AccountLedger,
    AccountRef,
    AccountContext,
    Environment,
    Reservation,
    ReservationBook,
    ReservationStatus,
)
from kairospy.orders import OrderJournal, OrderOrigin, OrderRequest, OrderSide, OrderState, OrderStatus, OrderType

from .coordinator import ExecutionCoordinator


@dataclass(frozen=True, slots=True)
class ExecutionStateSnapshot:
    orders: tuple[OrderState, ...] = ()
    ledger_events: tuple[AccountEvent, ...] = ()
    reservations: tuple[Reservation, ...] = ()

    @classmethod
    def capture(cls, coordinator: ExecutionCoordinator) -> "ExecutionStateSnapshot":
        return cls(
            orders=coordinator.orders.states,
            ledger_events=coordinator.ledger.events,
            reservations=coordinator.reservations.reservations,
        )

    def restore_into(self, coordinator: ExecutionCoordinator) -> ExecutionCoordinator:
        coordinator.orders = OrderJournal.from_states(self.orders)
        coordinator.ledger = AccountLedger(self.ledger_events)
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


@dataclass(frozen=True, slots=True)
class JsonExecutionStateStore:
    path: Path | str

    def load(self) -> ExecutionStateSnapshot | None:
        path = Path(self.path)
        if not path.exists():
            return None
        return ExecutionStateSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, coordinator: ExecutionCoordinator) -> ExecutionStateSnapshot:
        snapshot = ExecutionStateSnapshot.capture(coordinator)
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot.to_dict(), sort_keys=True, indent=2), encoding="utf-8")
        return snapshot


def _order_state_to_dict(state: OrderState) -> dict[str, object]:
    request = state.request
    return {
        "request": {
            "client_order_id": request.client_order_id,
            "context": _account_context_to_dict(request.context),
            "instrument_id": request.instrument_id,
            "side": request.side.value,
            "quantity": str(request.quantity),
            "order_type": request.order_type.value,
            "limit_price": None if request.limit_price is None else str(request.limit_price),
            "market_id": request.market_id,
            "reservation_id": request.reservation_id,
            "origin": request.origin.value,
            "venue_order_id": request.venue_order_id,
        },
        "status": state.status.value,
        "venue_order_id": state.venue_order_id,
        "filled_quantity": str(state.filled_quantity),
        "updated_at": None if state.updated_at is None else state.updated_at.isoformat(),
        "reason": state.reason,
    }


def _order_state_from_dict(value: Mapping[str, object]) -> OrderState:
    request_value = _mapping(value.get("request"))
    request = OrderRequest(
        str(request_value["client_order_id"]),
        _account_context_from_dict(_mapping(request_value["context"])),
        str(request_value["instrument_id"]),
        OrderSide(str(request_value["side"])),
        Decimal(str(request_value["quantity"])),
        order_type=OrderType(str(request_value["order_type"])),
        limit_price=_optional_decimal(request_value.get("limit_price")),
        market_id=_optional_text(request_value.get("market_id")),
        reservation_id=_optional_text(request_value.get("reservation_id")),
        origin=OrderOrigin(str(request_value.get("origin") or OrderOrigin.SYSTEM.value)),
        venue_order_id=_optional_text(request_value.get("venue_order_id")),
    )
    return OrderState(
        request,
        status=OrderStatus(str(value["status"])),
        venue_order_id=_optional_text(value.get("venue_order_id")),
        filled_quantity=Decimal(str(value.get("filled_quantity") or "0")),
        updated_at=_optional_datetime(value.get("updated_at")),
        reason=str(value.get("reason") or ""),
    )


def _account_event_to_dict(event: AccountEvent) -> dict[str, object]:
    return {
        "event_id": str(event.event_id),
        "account": _account_ref_to_dict(event.account),
        "kind": event.kind.value,
        "occurred_at": event.occurred_at.isoformat(),
        "currency": event.currency,
        "cash_delta": str(event.cash_delta),
        "instrument_id": event.instrument_id,
        "position_delta": str(event.position_delta),
        "reference_id": event.reference_id,
    }


def _account_event_from_dict(value: Mapping[str, object]) -> AccountEvent:
    return AccountEvent(
        UUID(str(value["event_id"])),
        _account_ref_from_dict(_mapping(value["account"])),
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
        "account": _account_ref_to_dict(reservation.account),
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
        _account_ref_from_dict(_mapping(value["account"])),
        str(value["currency"]),
        Decimal(str(value["amount"])),
        str(value["reason"]),
        _datetime(value["created_at"]),
        order_id=_optional_text(value.get("order_id")),
        status=ReservationStatus(str(value["status"])),
    )


def _account_context_to_dict(context: AccountContext) -> dict[str, object]:
    return {"account": _account_ref_to_dict(context.account), "environment": context.environment.value}


def _account_context_from_dict(value: Mapping[str, object]) -> AccountContext:
    return AccountContext(_account_ref_from_dict(_mapping(value["account"])), Environment(str(value["environment"])))


def _account_ref_to_dict(account: AccountRef) -> dict[str, object]:
    return {"broker": account.broker, "account_id": account.account_id, "segment": account.segment}


def _account_ref_from_dict(value: Mapping[str, object]) -> AccountRef:
    return AccountRef(str(value["broker"]), str(value["account_id"]), str(value.get("segment") or ""))


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


__all__ = ["ExecutionStateSnapshot", "JsonExecutionStateStore"]
