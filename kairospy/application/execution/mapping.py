from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from . import (
    ExecutionIntent,
    Fill,
    IntentStatus,
    Order,
    OrderSide,
    OrderStatus,
)
from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import (
    AccountId,
    FillId,
    InstrumentId,
    IntentId,
    OrderId,
    datetime_from_unix_nanos,
)


def map_execution_intent(value: object, *, event_sequence: int = 0) -> ExecutionIntent:
    root = _mapping(value, "Execution intent")
    intent_value = root.get("intent", root)
    intent = _mapping(intent_value, "Execution intent payload")
    status = _enum(
        IntentStatus,
        root.get("status", intent.get("status", "unknown")),
        IntentStatus.UNKNOWN,
    )
    instrument_id = _required(intent, "instrument_id")
    return ExecutionIntent(
        id=IntentId(_required(intent, "intent_id")),
        instrument=_instrument(instrument_id),
        account_ids=tuple(
            AccountId(str(item))
            for item in _sequence(intent.get("account_ids", ()), "account_ids")
        ),
        target_quantity=_decimal(intent.get("target_quantity")),
        status=status,
        reason=str(intent.get("reason", "")),
        order_ids=tuple(
            OrderId(str(item))
            for item in _sequence(root.get("order_ids", ()), "order_ids")
        ),
        event_sequence=event_sequence,
    )


def map_execution_order(value: object, *, event_sequence: int = 0) -> Order:
    row = _mapping(value, "Execution order")
    instrument_id = _required(row, "instrument_id")
    updated_nanos = row.get("updated_at_unix_nanos")
    updated_at = (
        datetime_from_unix_nanos(updated_nanos)
        if isinstance(updated_nanos, int)
        else None
    )
    raw_intent_id = row.get("intent_id")
    return Order(
        id=OrderId(_required(row, "order_id")),
        intent_id=IntentId(raw_intent_id)
        if isinstance(raw_intent_id, str) and raw_intent_id.strip()
        else None,
        instrument=_instrument(instrument_id),
        account_id=AccountId(_required(row, "account_id")),
        side=_enum(OrderSide, row.get("side"), OrderSide.BUY),
        quantity=_decimal(row.get("quantity")) or Decimal("0"),
        filled_quantity=_decimal(row.get("filled_quantity")) or Decimal("0"),
        limit_price=_decimal(row.get("limit_price")),
        status=_enum(OrderStatus, row.get("status"), OrderStatus.UNKNOWN),
        updated_at=updated_at,
        event_sequence=event_sequence,
    )


def map_execution_fill(value: object) -> Fill:
    row = _mapping(value, "Execution fill")
    nanos = row.get("occurred_at_unix_nanos")
    if not isinstance(nanos, int):
        raise ValueError("Execution fill occurred_at_unix_nanos must be an integer")
    instrument_id = _required(row, "instrument_id")
    return Fill(
        id=FillId(_required(row, "fill_id")),
        order_id=OrderId(_required(row, "order_id")),
        instrument=_instrument(instrument_id),
        quantity=_decimal(row.get("quantity")) or Decimal("0"),
        price=_decimal(row.get("price")) or Decimal("0"),
        occurred_at=datetime_from_unix_nanos(nanos),
    )


def _instrument(value: str) -> InstrumentRef:
    return InstrumentRef(InstrumentId(value), value.rsplit(":", 1)[-1])


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _required(value: Mapping[str, object], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"Execution value is missing {name}")
    return raw


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("decimal values must use the canonical string representation")
    return Decimal(value)


def _enum(enum_type, value: object, default):
    normalized = str(value or "").replace("_", "").lower()
    for member in enum_type:
        if member.value.replace("_", "").lower() == normalized:
            return member
    return default
