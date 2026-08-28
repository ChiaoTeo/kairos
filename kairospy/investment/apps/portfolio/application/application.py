from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from kairospy.investment.apps.account.application import (
    AccountApplication,
    DataFreshness,
    PositionSide,
    SegmentCompleteness,
)
from kairospy.contracts.account.events import AccountEvent
from kairospy.contracts.market.events import MarketEvent
from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.primitives.decimal import Money, Quantity, SignedQuantity
from kairospy.primitives.reference import AssetId
from kairospy.primitives.time import Generation, Sequence, UnixNanos

from .models import (
    AccountWatermark,
    PortfolioCash,
    PortfolioEquity,
    PortfolioEarnHolding,
    PortfolioFreshness,
    PortfolioHistoryPoint,
    PortfolioHolding,
    PortfolioSnapshot,
    SegmentWatermark,
    ValuationWatermark,
)


@dataclass(slots=True)
class _CashAccumulator:
    total: Quantity = field(default_factory=lambda: Quantity(0))
    available: Quantity = field(default_factory=lambda: Quantity(0))
    reserved: Quantity = field(default_factory=lambda: Quantity(0))


@dataclass(slots=True)
class _HoldingAccumulator:
    instrument: InstrumentRef
    net: SignedQuantity = field(default_factory=lambda: SignedQuantity(0))
    long: Quantity = field(default_factory=lambda: Quantity(0))
    short: Quantity = field(default_factory=lambda: Quantity(0))
    market_values: list[Money] = field(default_factory=list)
    pnls: list[Money] = field(default_factory=list)
    missing_market_value: bool = False
    missing_pnl: bool = False


class PortfolioApplication:
    """Instance-owned consolidated record derived from Account and Market facts."""

    def __init__(
        self,
        portfolio_id: str,
        account: AccountApplication,
        *,
        valuation_asset: str | None = None,
        history_limit: int = 4096,
    ) -> None:
        if not portfolio_id.strip():
            raise ValueError("portfolio_id is required")
        if valuation_asset is not None and not valuation_asset.strip():
            raise ValueError("valuation_asset must be non-empty when present")
        if history_limit <= 0:
            raise ValueError("history_limit must be positive")
        self.portfolio_id = portfolio_id
        self._account = account
        self._valuation_asset = valuation_asset
        self._version = 0
        self._valuation_watermark: ValuationWatermark | None = None
        self._history: deque[PortfolioHistoryPoint] = deque(maxlen=history_limit)
        self._legacy_equity_curve: list[dict[str, object]] = []
        self._snapshot = self._empty_snapshot()

    def snapshot(self) -> PortfolioSnapshot:
        return self._snapshot

    @property
    def history(self) -> tuple[PortfolioHistoryPoint, ...]:
        return tuple(self._history)

    @property
    def equity_curve(self) -> list[dict[str, object]]:
        """Compatibility view while backtest reports migrate to PortfolioSnapshot."""

        return self._legacy_equity_curve

    def require_current(self) -> PortfolioSnapshot:
        snapshot = self._snapshot
        if snapshot.freshness in {
            PortfolioFreshness.STALE,
            PortfolioFreshness.INCOMPLETE,
        }:
            raise RuntimeError(
                f"Portfolio {self.portfolio_id} is not current: "
                f"freshness={snapshot.freshness.value}"
            )
        return snapshot

    def rebuild(
        self, *, observed_at_unix_nanos: UnixNanos | int | None = None
    ) -> PortfolioSnapshot:
        observed_at = (
            None
            if observed_at_unix_nanos is None
            else UnixNanos(observed_at_unix_nanos)
        )
        accounts = self._account.snapshot().accounts
        self._version += 1
        if not accounts:
            self._snapshot = self._empty_snapshot(version=self._version)
            return self._snapshot

        cash: dict[AssetId, _CashAccumulator] = {}
        holdings: dict[str, _HoldingAccumulator] = {}
        equities: list[PortfolioEquity] = []
        earn_holdings: list[PortfolioEarnHolding] = []
        account_watermarks: list[AccountWatermark] = []
        all_fresh = True
        all_complete = True
        latest_observation = observed_at

        for account in accounts:
            segment_watermarks: list[SegmentWatermark] = []
            for segment in account.segments:
                all_fresh = all_fresh and segment.freshness is DataFreshness.FRESH
                all_complete = all_complete and segment.completeness in {
                    SegmentCompleteness.COMPLETE,
                    SegmentCompleteness.UNKNOWN,
                }
                for candidate in (
                    segment.last_event_at_unix_nanos,
                    segment.last_success_at_unix_nanos,
                ):
                    if candidate is not None:
                        candidate_time = UnixNanos(candidate)
                        latest_observation = max(
                            latest_observation or candidate_time, candidate_time
                        )
                segment_watermarks.append(
                    SegmentWatermark(
                        segment.segment_key,
                        Generation(segment.generation),
                        None
                        if segment.snapshot_watermark is None
                        else Sequence(segment.snapshot_watermark),
                        None
                        if segment.event_watermark is None
                        else Sequence(segment.event_watermark),
                        segment.freshness,
                        segment.completeness,
                    )
                )
                equities.append(
                    PortfolioEquity(
                        account.account_id,
                        segment.segment_key,
                        None if segment.equity is None else Money(segment.equity),
                    )
                )
                for balance in segment.balances:
                    asset = (
                        balance.asset
                        if isinstance(balance.asset, AssetId)
                        else AssetId(str(balance.asset))
                    )
                    totals = cash.setdefault(asset, _CashAccumulator())
                    totals.total = totals.total + Quantity(balance.total)
                    totals.available = totals.available + Quantity(balance.available)
                    totals.reserved = totals.reserved + Quantity(balance.reserved)
                earn_holdings.extend(
                    PortfolioEarnHolding(
                        account.account_id,
                        segment.segment_key,
                        holding.holding_key,
                        holding.product_id,
                        holding.asset
                        if isinstance(holding.asset, AssetId)
                        else AssetId(str(holding.asset)),
                        Quantity(holding.principal),
                        None
                        if holding.redeemable is None
                        else Quantity(holding.redeemable),
                        holding.state,
                        holding.liquidity,
                        None
                        if holding.observed_at_unix_nanos is None
                        else UnixNanos(holding.observed_at_unix_nanos),
                    )
                    for holding in segment.earn_holdings
                )
                for position in segment.positions:
                    key = str(position.instrument.id)
                    value = holdings.setdefault(key, _HoldingAccumulator(position.instrument))
                    signed_quantity = SignedQuantity(position.quantity)
                    quantity = Quantity(abs(signed_quantity.value))
                    if position.position_side is PositionSide.SHORT:
                        value.short = value.short + quantity
                        value.net = value.net - SignedQuantity(quantity.value)
                    elif position.position_side is PositionSide.LONG:
                        value.long = value.long + quantity
                        value.net = value.net + SignedQuantity(quantity.value)
                    else:
                        value.net = value.net + signed_quantity
                        if signed_quantity.value >= 0:
                            value.long = value.long + Quantity(signed_quantity.value)
                        else:
                            value.short = value.short + Quantity(abs(signed_quantity.value))
                    if position.market_value is None:
                        value.missing_market_value = True
                    else:
                        value.market_values.append(Money(position.market_value))
                    if position.unrealized_pnl is None:
                        value.missing_pnl = True
                    else:
                        value.pnls.append(Money(position.unrealized_pnl))
            account_watermarks.append(
                AccountWatermark(
                    account.account_id,
                    Generation(account.generation),
                    Sequence(account.event_sequence),
                    tuple(segment_watermarks),
                )
            )

        cash_values = tuple(
            PortfolioCash(asset, values.total, values.available, values.reserved)
            for asset, values in sorted(cash.items(), key=lambda item: str(item[0]))
        )
        holding_values = tuple(
            PortfolioHolding(
                value.instrument,
                value.net,
                value.long,
                value.short,
                None
                if value.missing_market_value
                else _sum_money(value.market_values),
                None
                if value.missing_pnl
                else _sum_money(value.pnls),
            )
            for _, value in sorted(holdings.items())
        )
        pnl_values = [
            value.unrealized_pnl
            for value in holding_values
            if value.unrealized_pnl is not None
        ]
        unrealized_pnl = (
            _sum_money(pnl_values)
            if len(pnl_values) == len(holding_values)
            else None
        )
        equity_values = [value.equity for value in equities if value.equity is not None]
        nav = (
            _sum_money(equity_values)
            if self._valuation_asset is not None and len(equity_values) == len(equities)
            else None
        )
        freshness = (
            PortfolioFreshness.INCOMPLETE
            if not all_complete
            else PortfolioFreshness.CURRENT
            if all_fresh
            else PortfolioFreshness.STALE
        )
        self._snapshot = PortfolioSnapshot(
            self.portfolio_id,
            self._version,
            tuple(account_watermarks),
            self._valuation_watermark,
            cash_values,
            holding_values,
            tuple(equities),
            tuple(
                sorted(
                    earn_holdings,
                    key=lambda value: (
                        str(value.account_id),
                        str(value.segment_key),
                        value.holding_key,
                    ),
                )
            ),
            nav,
            None,
            unrealized_pnl,
            freshness,
            all_complete,
            latest_observation,
        )
        return self._snapshot

    def observe(self, event: object) -> PortfolioSnapshot:
        if isinstance(event, AccountEvent):
            metadata = getattr(event, "metadata")
            observed_at_unix_nanos = getattr(
                metadata, "occurred_at_unix_nanos", None
            )
            if not isinstance(observed_at_unix_nanos, int):
                raise TypeError("Account native event omitted occurrence time")
            return self.rebuild(
                observed_at_unix_nanos=observed_at_unix_nanos
            )
        if isinstance(event, MarketEvent):
            self._valuation_watermark = ValuationWatermark(
                event.metadata.stream_id,
                Sequence(event.metadata.sequence),
                UnixNanos(event.metadata.occurred_at_unix_nanos),
            )
            return self.rebuild(
                observed_at_unix_nanos=event.metadata.occurred_at_unix_nanos
            )
        return self._snapshot

    def record_account_mark(
        self,
        account_snapshot: object,
        *,
        observed_at_unix_nanos: UnixNanos | int,
    ) -> PortfolioSnapshot:
        observed_at = UnixNanos(observed_at_unix_nanos)
        snapshot = self.rebuild(observed_at_unix_nanos=observed_at)
        self._history.append(PortfolioHistoryPoint(observed_at, snapshot))
        self._legacy_equity_curve.append(
            {
                "observed_at_unix_nanos": observed_at,
                "snapshot": account_snapshot,
            }
        )
        return snapshot

    def _empty_snapshot(self, *, version: int = 0) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            portfolio_id=self.portfolio_id,
            portfolio_version=version,
            account_watermarks=(),
            valuation_watermark=self._valuation_watermark,
            cash_by_asset=(),
            holdings_by_instrument=(),
            equity_by_location=(),
            earn_holdings=(),
            nav=Money(0) if self._valuation_asset is not None else None,
            realized_pnl=None,
            unrealized_pnl=Money(0),
            freshness=PortfolioFreshness.EMPTY,
            complete=True,
            observed_at_unix_nanos=None,
        )


def _sum_money(values: list[Money]) -> Money:
    result = Money(0)
    for value in values:
        result = result + value
    return result
