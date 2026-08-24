from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import AccountId, EventMetadata, InstrumentId, SegmentKey

from .events import (
    AccountEvent,
    AccountEventRecord,
    AccountStatusChangedEvent,
    BalanceChangedEvent,
    EarnHoldingChangedEvent,
    EquityChangedEvent,
    ObservedOrderChangedEvent,
    PositionChangedEvent,
)
from .models import (
    AccountSegmentSnapshot,
    AccountSnapshot,
    AccountsSnapshot,
    AccountStatusChange,
    Balance,
    DataFreshness,
    EarnHolding,
    EarnHoldingState,
    EarnLiquidity,
    EquityChange,
    ObservedOrder,
    Position,
    PositionSide,
)


def map_account_event(record: AccountEventRecord) -> tuple[AccountEvent, ...]:
    account_id = AccountId(record.account_id)
    metadata = EventMetadata(
        stream_id=record.stream_id,
        sequence=record.sequence,
        producer=record.producer,
        occurred_at_unix_nanos=record.occurred_at_unix_nanos,
    )
    events: list[AccountEvent] = []
    for change in record.changes:
        segment_key = SegmentKey(change.segment_key)
        if change.kind == "balance_changed":
            events.append(
                BalanceChangedEvent(
                    map_balance(
                        change.payload,
                        account_id=account_id,
                        segment_key=segment_key,
                    ),
                    metadata,
                )
            )
        elif change.kind == "balance_removed":
            row = _mapping(change.payload, "removed balance")
            events.append(
                BalanceChangedEvent(
                    Balance(
                        account_id,
                        segment_key,
                        str(row.get("asset_id", "")),
                        Decimal("0"),
                        Decimal("0"),
                        Decimal("0"),
                    ),
                    metadata,
                )
            )
        elif change.kind == "position_changed":
            events.append(
                PositionChangedEvent(
                    map_position(
                        change.payload,
                        account_id=account_id,
                        segment_key=segment_key,
                    ),
                    metadata,
                )
            )
        elif change.kind in {"earn_holding_changed", "earn_holding_removed"}:
            row = _mapping(change.payload, "Earn holding")
            removed = change.kind == "earn_holding_removed"
            events.append(
                EarnHoldingChangedEvent(
                    EarnHolding(
                        account_id=account_id,
                        segment_key=segment_key,
                        holding_key=str(row.get("holding_key", "")),
                        product_id=str(row.get("product_id", "")),
                        asset=str(row.get("asset", "")),
                        principal=Decimal("0")
                        if removed
                        else (_decimal(row.get("principal")) or Decimal("0")),
                        redeemable=None if removed else _decimal(row.get("redeemable")),
                        state=EarnHoldingState.REDEEMED
                        if removed
                        else EarnHoldingState(str(row.get("state", "unknown"))),
                        liquidity=EarnLiquidity(str(row.get("liquidity", "unknown"))),
                        participant_position_id=None
                        if removed
                        else _optional_text(row.get("participant_position_id")),
                        participant_state=None
                        if removed
                        else _optional_text(row.get("participant_state")),
                        notice_seconds=None
                        if removed
                        else _optional_int(row.get("notice_seconds")),
                        matures_at_unix_nanos=None
                        if removed
                        else _optional_int(row.get("matures_at_unix_nanos")),
                        observed_at_unix_nanos=None
                        if removed
                        else _optional_int(row.get("observed_at_unix_nanos")),
                    ),
                    metadata,
                )
            )
        elif change.kind == "position_removed":
            row = _mapping(change.payload, "removed position")
            instrument_id = str(row.get("instrument_id", ""))
            events.append(
                PositionChangedEvent(
                    Position(
                        account_id,
                        segment_key,
                        InstrumentRef(
                            InstrumentId(instrument_id),
                            instrument_id.rsplit(":", 1)[-1],
                        ),
                        Decimal("0"),
                        _position_side(row.get("position_side")),
                    ),
                    metadata,
                )
            )
        elif change.kind in {"observed_order_changed", "observed_order_removed"}:
            row = _mapping(change.payload, "observed order")
            instrument_id = str(row.get("instrument_id", row.get("order_id", "")))
            events.append(
                ObservedOrderChangedEvent(
                    ObservedOrder(
                        account_id=account_id,
                        segment_key=segment_key,
                        order_id=str(row.get("order_id", "")),
                        remote_order_id=_optional_text(row.get("remote_order_id")),
                        instrument=InstrumentRef(
                            InstrumentId(instrument_id),
                            instrument_id.rsplit(":", 1)[-1],
                        ),
                        market_id=str(row.get("market_id", "")),
                        quantity=_decimal(row.get("quantity")) or Decimal("0"),
                        filled_quantity=_decimal(row.get("filled_quantity"))
                        or Decimal("0"),
                        status=str(
                            row.get(
                                "status",
                                "closed"
                                if change.kind == "observed_order_removed"
                                else "unknown",
                            )
                        ),
                    ),
                    metadata,
                )
            )
        elif change.kind == "equity_changed":
            row = _mapping(change.payload, "equity change")
            events.append(
                EquityChangedEvent(
                    EquityChange(account_id, segment_key, _decimal(row.get("equity"))),
                    metadata,
                )
            )
        elif change.kind == "status_changed":
            row = _mapping(change.payload, "status change")
            events.append(
                AccountStatusChangedEvent(
                    AccountStatusChange(
                        account_id=account_id,
                        segment_key=segment_key,
                        freshness=_freshness(row),
                        trading_enabled=bool(row.get("trading_enabled", False)),
                        reason=(
                            None
                            if str(row.get("status", "")).lower() == "ready"
                            else str(row.get("status", "unknown")).lower()
                        ),
                    ),
                    metadata,
                )
            )
        else:
            raise ValueError(f"unsupported Account event kind: {change.kind}")
    return tuple(events)


def map_accounts_snapshot(
    value: object,
    *,
    enabled_account_ids: Iterable[AccountId | str] = (),
) -> AccountsSnapshot:
    """Map every enabled (account, segment) row without collapsing segments."""

    root = _mapping(value, "Accounts snapshot")
    root = _mapping(root.get("snapshot", root), "Accounts snapshot payload")
    generation = _integer(root.get("generation", 0), "generation")
    rows = tuple(
        _mapping(account, "account current view")
        for account in _sequence(root.get("accounts", ()), "accounts")
    )
    enabled = tuple(
        value if isinstance(value, AccountId) else AccountId(value)
        for value in enabled_account_ids
    )
    ordered_ids = enabled or tuple(
        dict.fromkeys(
            AccountId(str(row.get("account_id", "")))
            for row in rows
            if str(row.get("account_id", "")).strip()
        )
    )
    enabled_set = frozenset(ordered_ids)
    grouped: dict[AccountId, list[AccountSegmentSnapshot]] = {
        account_id: [] for account_id in ordered_ids
    }
    for row in rows:
        account_id = AccountId(_required_text(row.get("account_id"), "account_id"))
        if enabled_set and account_id not in enabled_set:
            continue
        grouped.setdefault(account_id, []).append(
            map_account_segment_snapshot(
                row, account_id=account_id, generation=generation
            )
        )
    accounts = tuple(
        AccountSnapshot(account_id, tuple(grouped.get(account_id, ())), generation)
        for account_id in ordered_ids
        if grouped.get(account_id)
    )
    return AccountsSnapshot(accounts)


def map_account_segment_snapshot(
    value: object,
    *,
    account_id: AccountId,
    generation: int,
) -> AccountSegmentSnapshot:
    row = _mapping(value, "Account segment snapshot")
    segment_key = SegmentKey(_required_text(row.get("segment_key"), "segment_key"))
    balances = tuple(
        map_balance(item, account_id=account_id, segment_key=segment_key)
        for item in _sequence(row.get("balances", ()), "balances")
    )
    positions = tuple(
        map_position(item, account_id=account_id, segment_key=segment_key)
        for item in _sequence(row.get("positions", ()), "positions")
    )
    earn_holdings = tuple(
        map_earn_holding(item, account_id=account_id, segment_key=segment_key)
        for item in _sequence(row.get("earn_holdings", ()), "earn_holdings")
    )
    observed_model = row.get("observed_account_model")
    configured_model = row.get("configured_account_model", row.get("account_model"))
    return AccountSegmentSnapshot(
        account_id=account_id,
        segment_key=segment_key,
        broker=str(row.get("broker", "")),
        environment=str(row.get("environment", "")),
        account_model=(
            str(observed_model)
            if observed_model is not None
            else None
            if configured_model is None
            else str(configured_model)
        ),
        equity=_decimal(row.get("equity")),
        balances=balances,
        positions=positions,
        earn_holdings=earn_holdings,
        earn_watermark_unix_nanos=_optional_int(row.get("earn_watermark_unix_nanos")),
        freshness=_freshness(row),
        generation=generation,
    )


def map_balance(
    value: object, *, account_id: AccountId, segment_key: SegmentKey
) -> Balance:
    row = _mapping(value, "balance")
    total = _decimal(row.get("total")) or Decimal("0")
    available = _decimal(row.get("available")) or Decimal("0")
    reserved = _decimal(row.get("reserved", row.get("locked")))
    return Balance(
        account_id,
        segment_key,
        str(row.get("asset", row.get("asset_code", row.get("symbol", "")))),
        total,
        available,
        total - available if reserved is None else reserved,
    )


def map_position(
    value: object, *, account_id: AccountId, segment_key: SegmentKey
) -> Position:
    row = _mapping(value, "position")
    instrument_id = str(row.get("instrument_id", row.get("symbol", "")))
    return Position(
        account_id=account_id,
        segment_key=segment_key,
        instrument=InstrumentRef(
            InstrumentId(instrument_id), instrument_id.rsplit(":", 1)[-1]
        ),
        quantity=_decimal(row.get("quantity")) or Decimal("0"),
        position_side=_position_side(row.get("position_side")),
        average_price=_decimal(row.get("average_price")),
        market_value=_decimal(row.get("market_value")),
        unrealized_pnl=_decimal(row.get("unrealized_pnl")),
    )


def map_earn_holding(
    value: object, *, account_id: AccountId, segment_key: SegmentKey
) -> EarnHolding:
    row = _mapping(value, "Earn holding")
    return EarnHolding(
        account_id=account_id,
        segment_key=segment_key,
        holding_key=_required_text(row.get("holding_key"), "earn holding_key"),
        participant_position_id=(
            None
            if row.get("participant_position_id") is None
            else str(row["participant_position_id"])
        ),
        product_id=_required_text(row.get("product_id"), "earn product_id"),
        asset=_required_text(row.get("asset"), "earn asset"),
        principal=_decimal(row.get("principal")) or Decimal("0"),
        redeemable=_decimal(row.get("redeemable")),
        state=EarnHoldingState(str(row.get("state", "unknown"))),
        participant_state=(
            None
            if row.get("participant_state") is None
            else str(row["participant_state"])
        ),
        liquidity=EarnLiquidity(str(row.get("liquidity", "unknown"))),
        notice_seconds=_optional_int(row.get("notice_seconds")),
        matures_at_unix_nanos=_optional_int(row.get("matures_at_unix_nanos")),
        observed_at_unix_nanos=_optional_int(row.get("observed_at_unix_nanos")),
    )


def _position_side(value: object) -> PositionSide:
    raw = str(value or "net").lower()
    if raw in {"both", "unspecified"}:
        raw = "net"
    try:
        return PositionSide(raw)
    except ValueError as exc:
        raise ValueError(f"unknown Account position side {value!r}") from exc


def _freshness(row: Mapping[str, object]) -> DataFreshness:
    raw = str(row.get("freshness", row.get("status", "unknown"))).lower()
    if bool(row.get("stale", False)):
        return DataFreshness.STALE
    if raw == "reconciling":
        return DataFreshness.RESYNCING
    if raw in {"unavailable", "suspended"}:
        return DataFreshness.UNAVAILABLE
    if raw == "ready":
        return DataFreshness.FRESH
    return (
        DataFreshness(raw)
        if raw in DataFreshness._value2member_map_
        else DataFreshness.UNKNOWN
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


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("optional integer value must be an integer")
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _required_text(value: object, name: str) -> str:
    result = value if isinstance(value, str) else ""
    if not result.strip():
        raise ValueError(f"Account snapshot {name} is required")
    return result
