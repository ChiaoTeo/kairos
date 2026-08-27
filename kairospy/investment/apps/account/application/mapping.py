from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol, cast

from kairospy.infrastructure.contracts.account.types import MarkToMarketRequest
from kairospy.infrastructure.contracts.market.events import MarketEvent
from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.reference import InstrumentId

from .models import (
    AccountSegmentSnapshot,
    AccountSnapshot,
    Balance,
    DataFreshness,
    EarnHolding,
    EarnHoldingState,
    EarnLiquidity,
    Position,
    PositionSide,
    SegmentCompleteness,
    SegmentSyncLifecycle,
    SegmentSyncMode,
)


class _MarketDecimal(Protocol):
    mantissa: int
    scale: int


class _MarketObservation(Protocol):
    instrument_id: str
    source_observed_at_unix_nanos: int


class _MarketBar(_MarketObservation, Protocol):
    close: _MarketDecimal


class _MarketQuote(_MarketObservation, Protocol):
    bid_price: _MarketDecimal | None
    ask_price: _MarketDecimal | None


def backtest_mark_to_market_request(
    event: object,
    *,
    segment_key: str = "spot",
    quote_asset: str = "USDT",
) -> object | None:
    """Adapt a Strategy Market observation to a typed Account simulation command."""

    if isinstance(event, MarketEvent) and event.kind == "bar":
        observation = cast(_MarketBar, event.data)
        mark = _market_decimal(observation.close)
    elif isinstance(event, MarketEvent) and event.kind == "quote":
        observation = cast(_MarketQuote, event.data)
        prices = tuple(
            _market_decimal(value)
            for value in (observation.bid_price, observation.ask_price)
            if value is not None
        )
        if not prices:
            return None
        mark = sum(prices, Decimal("0")) / len(prices)
    else:
        return None
    if not mark.is_finite():
        raise ValueError("decimal value must be finite")
    return MarkToMarketRequest(
        segment_key,
        str(observation.instrument_id),
        quote_asset,
        format(mark, "f"),
        observation.source_observed_at_unix_nanos,
    )


def map_account_snapshot(value: object, *, account_id: AccountId) -> AccountSnapshot:
    """Project one native owner snapshot into the Strategy read model."""

    actual_account_id = AccountId(
        _required_text(_field(value, "account_id"), "account_id")
    )
    if actual_account_id != account_id:
        raise ValueError("Account snapshot belongs to another account")
    generation = _integer(_field(value, "generation", 0), "generation")
    return AccountSnapshot(
        account_id=account_id,
        segments=tuple(
            map_account_segment_snapshot(
                item, account_id=account_id, generation=generation
            )
            for item in _sequence(_field(value, "segments", ()), "segments")
        ),
        generation=generation,
        event_sequence=_integer(_field(value, "event_sequence", 0), "event_sequence"),
    )


def map_account_segment_snapshot(
    value: object,
    *,
    account_id: AccountId,
    generation: int,
) -> AccountSegmentSnapshot:
    segment_key = SegmentKey(
        _required_text(_field(value, "segment_key"), "segment_key")
    )
    observed_model = _field(value, "observed_account_model", None)
    configured_model = _field(
        value, "configured_account_model", _field(value, "account_model", None)
    )
    return AccountSegmentSnapshot(
        account_id=account_id,
        segment_key=segment_key,
        broker=str(_field(value, "broker", "")),
        environment=str(_field(value, "environment", "")),
        account_model=(
            str(observed_model)
            if observed_model is not None
            else None
            if configured_model is None
            else str(configured_model)
        ),
        equity=_decimal(_field(value, "equity", None)),
        balances=tuple(
            _map_balance(item, account_id=account_id, segment_key=segment_key)
            for item in _sequence(_field(value, "balances", ()), "balances")
        ),
        positions=tuple(
            _map_position(item, account_id=account_id, segment_key=segment_key)
            for item in _sequence(_field(value, "positions", ()), "positions")
        ),
        earn_holdings=tuple(
            _map_earn_holding(item, account_id=account_id, segment_key=segment_key)
            for item in _sequence(_field(value, "earn_holdings", ()), "earn_holdings")
        ),
        earn_watermark_unix_nanos=_optional_int(
            _field(value, "earn_watermark_unix_nanos", None)
        ),
        freshness=_freshness(value),
        generation=generation,
        sync_mode=SegmentSyncMode(str(_field(value, "sync_mode", "unknown"))),
        sync_lifecycle=SegmentSyncLifecycle(
            str(_field(value, "sync_lifecycle", "configured"))
        ),
        completeness=SegmentCompleteness(str(_field(value, "completeness", "unknown"))),
        snapshot_watermark=_optional_int(_field(value, "snapshot_watermark", None)),
        event_watermark=_optional_int(_field(value, "event_watermark", None)),
        channel_epoch=_optional_int(_field(value, "channel_epoch", None)),
        last_event_at_unix_nanos=_optional_int(
            _field(value, "last_event_at_unix_nanos", None)
        ),
        last_success_at_unix_nanos=_optional_int(
            _field(value, "last_success_at_unix_nanos", None)
        ),
        last_error=_optional_text(_field(value, "last_error", None)),
        recovery_buffer_depth=_integer(
            _field(value, "recovery_buffer_depth", 0), "recovery_buffer_depth"
        ),
    )


def _map_balance(
    value: object, *, account_id: AccountId, segment_key: SegmentKey
) -> Balance:
    total = _decimal(_field(value, "total", None)) or Decimal("0")
    available = _decimal(_field(value, "available", None)) or Decimal("0")
    reserved = _decimal(_field(value, "reserved", _field(value, "locked", None)))
    return Balance(
        account_id,
        segment_key,
        str(
            _field(
                value,
                "asset",
                _field(value, "asset_code", _field(value, "symbol", "")),
            )
        ),
        total,
        available,
        total - available if reserved is None else reserved,
    )


def _map_position(
    value: object, *, account_id: AccountId, segment_key: SegmentKey
) -> Position:
    instrument_id = str(_field(value, "instrument_id", _field(value, "symbol", "")))
    return Position(
        account_id=account_id,
        segment_key=segment_key,
        instrument=InstrumentRef(
            InstrumentId(instrument_id), instrument_id.rsplit(":", 1)[-1]
        ),
        quantity=_decimal(_field(value, "quantity", None)) or Decimal("0"),
        position_side=_position_side(_field(value, "position_side", None)),
        average_price=_decimal(_field(value, "average_price", None)),
        market_value=_decimal(_field(value, "market_value", None)),
        unrealized_pnl=_decimal(_field(value, "unrealized_pnl", None)),
    )


def _map_earn_holding(
    value: object, *, account_id: AccountId, segment_key: SegmentKey
) -> EarnHolding:
    participant_position_id = _field(value, "participant_position_id", None)
    participant_state = _field(value, "participant_state", None)
    return EarnHolding(
        account_id=account_id,
        segment_key=segment_key,
        holding_key=_required_text(_field(value, "holding_key"), "earn holding_key"),
        participant_position_id=(
            None if participant_position_id is None else str(participant_position_id)
        ),
        product_id=_required_text(_field(value, "product_id"), "earn product_id"),
        asset=_required_text(_field(value, "asset"), "earn asset"),
        principal=_decimal(_field(value, "principal", None)) or Decimal("0"),
        redeemable=_decimal(_field(value, "redeemable", None)),
        state=EarnHoldingState(str(_field(value, "state", "unknown"))),
        participant_state=(
            None if participant_state is None else str(participant_state)
        ),
        liquidity=EarnLiquidity(str(_field(value, "liquidity", "unknown"))),
        notice_seconds=_optional_int(_field(value, "notice_seconds", None)),
        matures_at_unix_nanos=_optional_int(
            _field(value, "matures_at_unix_nanos", None)
        ),
        observed_at_unix_nanos=_optional_int(
            _field(value, "observed_at_unix_nanos", None)
        ),
    )


def _market_decimal(value: object) -> Decimal:
    return Decimal(int(getattr(value, "mantissa"))).scaleb(
        -int(getattr(value, "scale"))
    )


def _position_side(value: object) -> PositionSide:
    raw = str(value or "net").lower()
    if raw in {"both", "unspecified"}:
        raw = "net"
    try:
        return PositionSide(raw)
    except ValueError as error:
        raise ValueError(f"unknown Account position side {value!r}") from error


def _freshness(value: object) -> DataFreshness:
    raw = str(_field(value, "freshness", _field(value, "status", "unknown"))).lower()
    if bool(_field(value, "stale", False)):
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


_MISSING = object()


def _field(value: object, name: str, default: object = _MISSING) -> object:
    if hasattr(value, name):
        return getattr(value, name)
    if default is not _MISSING:
        return default
    raise ValueError(f"Account value omitted {name}")


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        return Decimal(value)
    decimal_value = getattr(value, "value", None)
    if isinstance(decimal_value, Decimal):
        return decimal_value
    mantissa = getattr(value, "mantissa", None)
    scale = getattr(value, "scale", None)
    if isinstance(mantissa, bool) or not isinstance(mantissa, int):
        raise ValueError("decimal mantissa must be an integer")
    if isinstance(scale, bool) or not isinstance(scale, int):
        raise ValueError("decimal scale must be an integer")
    return Decimal(mantissa).scaleb(-scale)


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
    semantic_value = getattr(value, "value", value)
    result = semantic_value if isinstance(semantic_value, str) else ""
    if not result.strip():
        raise ValueError(f"Account snapshot {name} is required")
    return result
