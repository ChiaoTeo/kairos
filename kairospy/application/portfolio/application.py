from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal
from typing import cast

from kairospy.application.account import (
    AccountApplication,
    AccountStatusChangedEvent,
    BalanceChangedEvent,
    DataFreshness,
    EquityChangedEvent,
    EarnHoldingChangedEvent,
    ObservedOrderChangedEvent,
    PositionChangedEvent,
    PositionSide,
    SegmentCompleteness,
)
from kairospy.application.market import BarEvent, GreeksEvent, QuoteEvent, TradeEvent
from kairospy.application.reference import InstrumentRef

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


_ACCOUNT_EVENT_TYPES = (
    AccountStatusChangedEvent,
    BalanceChangedEvent,
    EquityChangedEvent,
    EarnHoldingChangedEvent,
    ObservedOrderChangedEvent,
    PositionChangedEvent,
)
_MARKET_EVENT_TYPES = (BarEvent, GreeksEvent, QuoteEvent, TradeEvent)


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
        self, *, observed_at_unix_nanos: int | None = None
    ) -> PortfolioSnapshot:
        accounts = self._account.snapshot().accounts
        self._version += 1
        if not accounts:
            self._snapshot = self._empty_snapshot(version=self._version)
            return self._snapshot

        cash: dict[str, list[Decimal]] = defaultdict(
            lambda: [Decimal("0"), Decimal("0"), Decimal("0")]
        )
        holdings: dict[str, dict[str, object]] = {}
        equities: list[PortfolioEquity] = []
        earn_holdings: list[PortfolioEarnHolding] = []
        account_watermarks: list[AccountWatermark] = []
        all_fresh = True
        all_complete = True
        latest_observation = observed_at_unix_nanos

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
                        latest_observation = max(
                            latest_observation or candidate, candidate
                        )
                segment_watermarks.append(
                    SegmentWatermark(
                        segment.segment_key,
                        segment.generation,
                        segment.snapshot_watermark,
                        segment.event_watermark,
                        segment.freshness,
                        segment.completeness,
                    )
                )
                equities.append(
                    PortfolioEquity(
                        account.account_id, segment.segment_key, segment.equity
                    )
                )
                for balance in segment.balances:
                    totals = cash[balance.asset]
                    totals[0] += balance.total
                    totals[1] += balance.available
                    totals[2] += balance.reserved
                earn_holdings.extend(
                    PortfolioEarnHolding(
                        account.account_id,
                        segment.segment_key,
                        holding.holding_key,
                        holding.product_id,
                        holding.asset,
                        holding.principal,
                        holding.redeemable,
                        holding.state,
                        holding.liquidity,
                        holding.observed_at_unix_nanos,
                    )
                    for holding in segment.earn_holdings
                )
                for position in segment.positions:
                    key = str(position.instrument.id)
                    value = holdings.setdefault(
                        key,
                        {
                            "instrument": position.instrument,
                            "net": Decimal("0"),
                            "long": Decimal("0"),
                            "short": Decimal("0"),
                            "market_values": [],
                            "pnls": [],
                            "missing_market_value": False,
                            "missing_pnl": False,
                        },
                    )
                    quantity = abs(position.quantity)
                    if position.position_side is PositionSide.SHORT:
                        value["short"] = cast(Decimal, value["short"]) + quantity
                        value["net"] = cast(Decimal, value["net"]) - quantity
                    elif position.position_side is PositionSide.LONG:
                        value["long"] = cast(Decimal, value["long"]) + quantity
                        value["net"] = cast(Decimal, value["net"]) + quantity
                    else:
                        value["net"] = cast(Decimal, value["net"]) + position.quantity
                        if position.quantity >= 0:
                            value["long"] = (
                                cast(Decimal, value["long"]) + position.quantity
                            )
                        else:
                            value["short"] = cast(Decimal, value["short"]) + abs(
                                position.quantity
                            )
                    if position.market_value is None:
                        value["missing_market_value"] = True
                    else:
                        cast(list[Decimal], value["market_values"]).append(
                            position.market_value
                        )
                    if position.unrealized_pnl is None:
                        value["missing_pnl"] = True
                    else:
                        cast(list[Decimal], value["pnls"]).append(
                            position.unrealized_pnl
                        )
            account_watermarks.append(
                AccountWatermark(
                    account.account_id,
                    account.generation,
                    account.event_sequence,
                    tuple(segment_watermarks),
                )
            )

        cash_values = tuple(
            PortfolioCash(asset, values[0], values[1], values[2])
            for asset, values in sorted(cash.items())
        )
        holding_values = tuple(
            PortfolioHolding(
                cast("InstrumentRef", value["instrument"]),
                cast(Decimal, value["net"]),
                cast(Decimal, value["long"]),
                cast(Decimal, value["short"]),
                None
                if value["missing_market_value"]
                else sum(cast(list[Decimal], value["market_values"]), Decimal("0")),
                None
                if value["missing_pnl"]
                else sum(cast(list[Decimal], value["pnls"]), Decimal("0")),
            )
            for _, value in sorted(holdings.items())
        )
        pnl_values = [
            value.unrealized_pnl
            for value in holding_values
            if value.unrealized_pnl is not None
        ]
        unrealized_pnl = (
            sum(pnl_values, Decimal("0"))
            if len(pnl_values) == len(holding_values)
            else None
        )
        equity_values = [value.equity for value in equities if value.equity is not None]
        nav = (
            sum(equity_values, Decimal("0"))
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
        if isinstance(event, _ACCOUNT_EVENT_TYPES):
            return self.rebuild(
                observed_at_unix_nanos=event.metadata.occurred_at_unix_nanos
            )
        if isinstance(event, _MARKET_EVENT_TYPES):
            self._valuation_watermark = ValuationWatermark(
                event.metadata.stream_id,
                event.metadata.sequence,
                event.metadata.occurred_at_unix_nanos,
            )
            return self.rebuild(
                observed_at_unix_nanos=event.metadata.occurred_at_unix_nanos
            )
        return self._snapshot

    def record_account_mark(
        self, account_snapshot: object, *, observed_at_unix_nanos: int
    ) -> PortfolioSnapshot:
        snapshot = self.rebuild(observed_at_unix_nanos=observed_at_unix_nanos)
        self._history.append(PortfolioHistoryPoint(observed_at_unix_nanos, snapshot))
        self._legacy_equity_curve.append(
            {
                "observed_at_unix_nanos": observed_at_unix_nanos,
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
            nav=Decimal("0") if self._valuation_asset is not None else None,
            realized_pnl=None,
            unrealized_pnl=Decimal("0"),
            freshness=PortfolioFreshness.EMPTY,
            complete=True,
            observed_at_unix_nanos=None,
        )
