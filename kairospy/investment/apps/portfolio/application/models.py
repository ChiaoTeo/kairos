from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kairospy.investment.apps.account.application import (
    DataFreshness,
    EarnHoldingState,
    EarnLiquidity,
    SegmentCompleteness,
)
from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.primitives.account import AccountId, SegmentKey


class PortfolioFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    INCOMPLETE = "incomplete"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class SegmentWatermark:
    segment_key: SegmentKey
    generation: int
    snapshot_watermark: int | None
    event_watermark: int | None
    freshness: DataFreshness
    completeness: SegmentCompleteness


@dataclass(frozen=True, slots=True)
class AccountWatermark:
    account_id: AccountId
    generation: int
    event_sequence: int
    segments: tuple[SegmentWatermark, ...]


@dataclass(frozen=True, slots=True)
class ValuationWatermark:
    stream_id: str
    sequence: int
    occurred_at_unix_nanos: int | None


@dataclass(frozen=True, slots=True)
class PortfolioCash:
    asset: str
    total: Decimal
    available: Decimal
    reserved: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioHolding:
    instrument: InstrumentRef
    net_quantity: Decimal
    long_quantity: Decimal
    short_quantity: Decimal
    market_value: Decimal | None
    unrealized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class PortfolioEquity:
    account_id: AccountId
    segment_key: SegmentKey
    equity: Decimal | None


@dataclass(frozen=True, slots=True)
class PortfolioEarnHolding:
    account_id: AccountId
    segment_key: SegmentKey
    holding_key: str
    product_id: str
    asset: str
    principal: Decimal
    redeemable: Decimal | None
    state: EarnHoldingState
    liquidity: EarnLiquidity
    observed_at_unix_nanos: int | None


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
    nav: Decimal | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    freshness: PortfolioFreshness
    complete: bool
    observed_at_unix_nanos: int | None

    def __post_init__(self) -> None:
        if not self.portfolio_id.strip():
            raise ValueError("portfolio_id is required")
        if self.portfolio_version < 0:
            raise ValueError("portfolio_version cannot be negative")

    def cash(self, asset: str) -> PortfolioCash | None:
        return next(
            (value for value in self.cash_by_asset if value.asset == asset), None
        )

    def holding(self, instrument_id: str) -> PortfolioHolding | None:
        return next(
            (
                value
                for value in self.holdings_by_instrument
                if str(value.instrument.id) == instrument_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class PortfolioHistoryPoint:
    observed_at_unix_nanos: int
    portfolio: PortfolioSnapshot
