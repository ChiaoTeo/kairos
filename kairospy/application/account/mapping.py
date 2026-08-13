from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import AccountId, InstrumentId

from .models import AccountSnapshot, Balance, DataFreshness, Position


def map_account_snapshot(value: object, *, account_id: AccountId) -> AccountSnapshot:
    root = _mapping(value, "Account snapshot")
    root = _mapping(root.get("snapshot", root), "Account snapshot payload")
    balances = tuple(
        map_balance(item, account_id=account_id)
        for item in _sequence(root.get("balances", ()), "balances")
    )
    positions = tuple(
        map_position(item, account_id=account_id)
        for item in _sequence(root.get("positions", ()), "positions")
    )
    raw_freshness = str(root.get("freshness", "unknown")).lower()
    freshness = (
        DataFreshness(raw_freshness)
        if raw_freshness in DataFreshness._value2member_map_
        else DataFreshness.UNKNOWN
    )
    return AccountSnapshot(
        account_id=account_id,
        equity=_decimal(root.get("equity")),
        balances=balances,
        positions=positions,
        freshness=freshness,
        generation=_integer(root.get("generation", 0), "generation"),
        event_sequence=_integer(root.get("event_sequence", 0), "event_sequence"),
    )


def map_balance(value: object, *, account_id: AccountId) -> Balance:
    row = _mapping(value, "balance")
    total = _decimal(row.get("total")) or Decimal("0")
    available = _decimal(row.get("available")) or Decimal("0")
    reserved = _decimal(row.get("reserved"))
    return Balance(
        account_id,
        str(row.get("asset", row.get("symbol", ""))),
        total,
        available,
        total - available if reserved is None else reserved,
    )


def map_position(value: object, *, account_id: AccountId) -> Position:
    row = _mapping(value, "position")
    instrument_id = str(row.get("instrument_id", row.get("symbol", "")))
    return Position(
        account_id=account_id,
        instrument=InstrumentRef(
            InstrumentId(instrument_id), instrument_id.rsplit(":", 1)[-1]
        ),
        quantity=_decimal(row.get("quantity")) or Decimal("0"),
        average_price=_decimal(row.get("average_price")),
        market_value=_decimal(row.get("market_value")),
        unrealized_pnl=_decimal(row.get("unrealized_pnl")),
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("decimal values must use the canonical string representation")
    return Decimal(value)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value
