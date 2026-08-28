from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kairospy.investment.apps.account.application import (
    DataFreshness,
    EarnHoldingState,
    EarnLiquidity,
    SegmentCompleteness,
)
from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.capital import EarnProductId
from kairospy.primitives.decimal import Money, Quantity, SignedQuantity
from kairospy.primitives.reference import AssetId, InstrumentId
from kairospy.primitives.time import Generation, Sequence, UnixNanos


class PortfolioFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    INCOMPLETE = "incomplete"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class SegmentWatermark:
    segment_key: SegmentKey
    generation: Generation
    snapshot_watermark: Sequence | None
    event_watermark: Sequence | None
    freshness: DataFreshness
    completeness: SegmentCompleteness


@dataclass(frozen=True, slots=True)
class AccountWatermark:
    account_id: AccountId
    generation: Generation
    event_sequence: Sequence
    segments: tuple[SegmentWatermark, ...]


@dataclass(frozen=True, slots=True)
class ValuationWatermark:
    stream_id: str
    sequence: Sequence
    occurred_at_unix_nanos: UnixNanos | None


@dataclass(frozen=True, slots=True)
class PortfolioCash:
    asset: AssetId
    total: Quantity
    available: Quantity
    reserved: Quantity


@dataclass(frozen=True, slots=True)
class PortfolioHolding:
    instrument: InstrumentRef
    net_quantity: SignedQuantity
    long_quantity: Quantity
    short_quantity: Quantity
    market_value: Money | None
    unrealized_pnl: Money | None


@dataclass(frozen=True, slots=True)
class PortfolioEquity:
    account_id: AccountId
    segment_key: SegmentKey
    equity: Money | None


@dataclass(frozen=True, slots=True)
class PortfolioEarnHolding:
    account_id: AccountId
    segment_key: SegmentKey
    holding_key: str
    product_id: EarnProductId
    asset: AssetId
    principal: Quantity
    redeemable: Quantity | None
    state: EarnHoldingState
    liquidity: EarnLiquidity
    observed_at_unix_nanos: UnixNanos | None


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    portfolio_id: str
    portfolio_version: int
    account_watermarks: tuple[AccountWatermark, ...]
    valuation_watermark: ValuationWatermark | None
    cash_by_asset: tuple[PortfolioCash, ...]
    holdings_by_instrument: tuple[PortfolioHolding, ...]
    equity_by_location: tuple[PortfolioEquity, ...]
    earn_holdings: tuple[PortfolioEarnHolding, ...]
    nav: Money | None
    realized_pnl: Money | None
    unrealized_pnl: Money | None
    freshness: PortfolioFreshness
    complete: bool
    observed_at_unix_nanos: UnixNanos | None

    def __post_init__(self) -> None:
        if not self.portfolio_id.strip():
            raise ValueError("portfolio_id is required")
        if self.portfolio_version < 0:
            raise ValueError("portfolio_version cannot be negative")

    def cash(self, asset: AssetId | str) -> PortfolioCash | None:
        asset_id = asset if isinstance(asset, AssetId) else AssetId(asset)
        return next(
            (value for value in self.cash_by_asset if value.asset == asset_id), None
        )

    def holding(
        self, instrument_id: InstrumentId | str
    ) -> PortfolioHolding | None:
        identity = (
            instrument_id
            if isinstance(instrument_id, InstrumentId)
            else InstrumentId(instrument_id)
        )
        return next(
            (
                value
                for value in self.holdings_by_instrument
                if value.instrument.id == identity
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class PortfolioHistoryPoint:
    observed_at_unix_nanos: UnixNanos
    portfolio: PortfolioSnapshot
