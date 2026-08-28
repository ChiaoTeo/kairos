from __future__ import annotations

from decimal import Decimal
from typing import Protocol, cast

from kairospy.contracts.account.types import (
    AccountBalanceCurrent,
    AccountCurrentSnapshot,
    AccountEarnHoldingCurrent,
    AccountPositionCurrent,
    AccountSegmentCurrent,
    MarkToMarketRequest,
)
from kairospy.contracts.market.events import MarketEvent
from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.primitives.account import AccountId, BrokerId, SegmentKey
from kairospy.primitives.capital import EarnProductId
from kairospy.primitives.decimal import Price, PriceLike
from kairospy.primitives.reference import AssetId, InstrumentId, InstrumentIdRead
from kairospy.primitives.time import Generation, Sequence, UnixNanos, UnixNanosRead

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


class _MarketObservation(Protocol):
    instrument_id: InstrumentIdRead
    source_observed_at_unix_nanos: UnixNanosRead


class _MarketBar(_MarketObservation, Protocol):
    close: PriceLike


class _MarketQuote(_MarketObservation, Protocol):
    bid_price: PriceLike | None
    ask_price: PriceLike | None


def backtest_mark_to_market_request(
    event: object,
    *,
    segment_key: SegmentKey | str = "spot",
    quote_asset: AssetId | str = "USDT",
) -> MarkToMarketRequest | None:
    """Adapt a Strategy Market observation to a typed Account simulation command."""

    if isinstance(event, MarketEvent) and event.kind == "bar_completed":
        observation = cast(_MarketBar, event.data)
        mark: PriceLike = observation.close
    elif isinstance(event, MarketEvent) and event.kind == "quote_updated":
        observation = cast(_MarketQuote, event.data)
        prices = tuple(
            value.value
            for value in (observation.bid_price, observation.ask_price)
            if value is not None
        )
        if not prices:
            return None
        mark = Price(sum(prices, Decimal(0)) / len(prices))
    else:
        return None
    return MarkToMarketRequest(
        str(segment_key),
        str(observation.instrument_id),
        str(quote_asset),
        mark,
        observation.source_observed_at_unix_nanos,
    )


def map_account_snapshot(
    value: AccountCurrentSnapshot, *, account_id: AccountId
) -> AccountSnapshot:
    """Project one native owner snapshot into the Strategy read model."""

    actual_account_id = AccountId(value.account_id)
    if actual_account_id != account_id:
        raise ValueError("Account snapshot belongs to another account")
    generation = Generation(value.generation)
    return AccountSnapshot(
        account_id=account_id,
        segments=tuple(
            map_account_segment_snapshot(
                item, account_id=account_id, generation=generation
            )
            for item in value.segments
        ),
        generation=generation,
        event_sequence=Sequence(value.event_sequence),
    )


def map_account_segment_snapshot(
    value: AccountSegmentCurrent,
    *,
    account_id: AccountId,
    generation: Generation,
) -> AccountSegmentSnapshot:
    segment_key = SegmentKey(value.segment_key)
    return AccountSegmentSnapshot(
        account_id=account_id,
        segment_key=segment_key,
        broker=BrokerId(value.broker),
        environment=value.environment,
        account_model=value.account_model,
        equity=value.equity,
        balances=tuple(
            _map_balance(item, account_id=account_id, segment_key=segment_key)
            for item in value.balances
        ),
        positions=tuple(
            _map_position(item, account_id=account_id, segment_key=segment_key)
            for item in value.positions
        ),
        earn_holdings=tuple(
            _map_earn_holding(item, account_id=account_id, segment_key=segment_key)
            for item in value.earn_holdings
        ),
        earn_watermark_unix_nanos=_unix_nanos(value.earn_watermark_unix_nanos),
        freshness=_freshness(value.freshness),
        generation=generation,
        sync_mode=SegmentSyncMode(value.sync_mode),
        sync_lifecycle=SegmentSyncLifecycle(value.sync_lifecycle),
        completeness=SegmentCompleteness(value.completeness),
        snapshot_watermark=_sequence(value.snapshot_watermark),
        event_watermark=_sequence(value.event_watermark),
        channel_epoch=value.channel_epoch,
        last_event_at_unix_nanos=_unix_nanos(value.last_event_at_unix_nanos),
        last_success_at_unix_nanos=_unix_nanos(value.last_success_at_unix_nanos),
        last_error=value.last_error,
        recovery_buffer_depth=value.recovery_buffer_depth,
    )


def _map_balance(
    value: AccountBalanceCurrent,
    *,
    account_id: AccountId,
    segment_key: SegmentKey,
) -> Balance:
    return Balance(
        account_id,
        segment_key,
        AssetId(value.asset),
        value.total,
        value.available,
        value.reserved,
    )


def _map_position(
    value: AccountPositionCurrent,
    *,
    account_id: AccountId,
    segment_key: SegmentKey,
) -> Position:
    instrument_id = InstrumentId(value.instrument_id)
    return Position(
        account_id=account_id,
        segment_key=segment_key,
        instrument=InstrumentRef(instrument_id, str(instrument_id).rsplit(":", 1)[-1]),
        quantity=value.quantity,
        position_side=_position_side(value.position_side),
        average_price=value.average_price,
        market_value=value.market_value,
        unrealized_pnl=value.unrealized_pnl,
    )


def _map_earn_holding(
    value: AccountEarnHoldingCurrent,
    *,
    account_id: AccountId,
    segment_key: SegmentKey,
) -> EarnHolding:
    return EarnHolding(
        account_id=account_id,
        segment_key=segment_key,
        holding_key=value.holding_key,
        participant_position_id=value.participant_position_id,
        product_id=EarnProductId(value.product_id),
        asset=AssetId(value.asset),
        principal=value.principal,
        redeemable=value.redeemable,
        state=EarnHoldingState(value.state),
        participant_state=value.participant_state,
        liquidity=EarnLiquidity(value.liquidity),
        notice_seconds=value.notice_seconds,
        matures_at_unix_nanos=_unix_nanos(value.matures_at_unix_nanos),
        observed_at_unix_nanos=_unix_nanos(value.observed_at_unix_nanos),
    )


def _position_side(value: str) -> PositionSide:
    raw = value.lower()
    if raw in {"both", "unspecified"}:
        raw = "net"
    try:
        return PositionSide(raw)
    except ValueError as error:
        raise ValueError(f"unknown Account position side {value!r}") from error


def _freshness(value: str) -> DataFreshness:
    raw = value.lower()
    if raw == "reconciling":
        return DataFreshness.RESYNCING
    if raw in {"unavailable", "suspended"}:
        return DataFreshness.UNAVAILABLE
    if raw == "ready":
        return DataFreshness.FRESH
    return DataFreshness(raw) if raw in DataFreshness._value2member_map_ else DataFreshness.UNKNOWN


def _sequence(value: int | None) -> Sequence | None:
    return None if value is None else Sequence(value)


def _unix_nanos(value: int | None) -> UnixNanos | None:
    return None if value is None else UnixNanos(value)
