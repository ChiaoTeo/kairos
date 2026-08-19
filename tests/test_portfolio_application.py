from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kairospy.application.account import (
    AccountApplication,
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
    SPOT,
    USD_M_FUTURES,
)
from kairospy.application.market import ObservationScope, Quote, QuoteEvent
from kairospy.application.portfolio import (
    PortfolioApplication,
    PortfolioFreshness,
)
from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import (
    AccountId,
    EventMetadata,
    InstrumentId,
    MarketId,
)


class _Projection:
    def __init__(self, snapshot: AccountSnapshot) -> None:
        self.value = snapshot

    def snapshot(self, account_id: AccountId) -> AccountSnapshot:
        assert account_id == self.value.account_id
        return self.value


def _account(
    account: str,
    segment,
    *,
    generation: int,
    equity: str,
    balance: str,
    quantity: str,
    side: PositionSide,
    freshness: DataFreshness = DataFreshness.FRESH,
) -> AccountSnapshot:
    account_id = AccountId(account)
    instrument = InstrumentRef(InstrumentId("instrument:test:BTCUSDT"), "BTCUSDT")
    return AccountSnapshot(
        account_id,
        (
            AccountSegmentSnapshot(
                account_id=account_id,
                segment_key=segment,
                broker="paper",
                environment="paper",
                account_model="no_margin",
                equity=Decimal(equity),
                balances=(
                    Balance(
                        account_id,
                        segment,
                        "USDT",
                        Decimal(balance),
                        Decimal(balance) - Decimal("10"),
                        Decimal("10"),
                    ),
                ),
                positions=(
                    Position(
                        account_id,
                        segment,
                        instrument,
                        Decimal(quantity),
                        side,
                        market_value=Decimal(quantity) * Decimal("100"),
                        unrealized_pnl=Decimal("5"),
                    ),
                ),
                freshness=freshness,
                generation=generation,
                sync_lifecycle=SegmentSyncLifecycle.LIVE,
                completeness=SegmentCompleteness.COMPLETE,
                snapshot_watermark=generation * 10,
                event_watermark=generation * 10 + 1,
                last_event_at_unix_nanos=generation * 100,
            ),
        ),
        generation,
        event_sequence=generation * 10 + 1,
    )


def test_portfolio_rebuilds_one_record_across_accounts_and_segments() -> None:
    main = _account(
        "main",
        SPOT,
        generation=3,
        equity="1000",
        balance="100",
        quantity="2",
        side=PositionSide.LONG,
    )
    hedge = _account(
        "hedge",
        USD_M_FUTURES,
        generation=7,
        equity="2000",
        balance="50",
        quantity="0.5",
        side=PositionSide.SHORT,
    )
    portfolio = PortfolioApplication(
        "paper:strategy-a",
        AccountApplication(
            {
                main.account_id: _Projection(main),
                hedge.account_id: _Projection(hedge),
            }
        ),
        valuation_asset="USDT",
    )

    snapshot = portfolio.rebuild()

    assert snapshot.portfolio_version == 1
    assert snapshot.freshness is PortfolioFreshness.CURRENT
    assert snapshot.complete is True
    assert snapshot.nav == Decimal("3000")
    assert snapshot.cash("USDT") is not None
    assert snapshot.cash("USDT").total == Decimal("150")
    holding = snapshot.holding("instrument:test:BTCUSDT")
    assert holding is not None
    assert holding.long_quantity == Decimal("2")
    assert holding.short_quantity == Decimal("0.5")
    assert holding.net_quantity == Decimal("1.5")
    assert [value.generation for value in snapshot.account_watermarks] == [3, 7]
    assert [value.event_sequence for value in snapshot.account_watermarks] == [31, 71]


def test_portfolio_preserves_staleness_instead_of_inventing_global_freshness() -> None:
    stale = _account(
        "stale",
        SPOT,
        generation=2,
        equity="100",
        balance="100",
        quantity="0",
        side=PositionSide.NET,
        freshness=DataFreshness.STALE,
    )
    portfolio = PortfolioApplication(
        "live:strategy-a",
        AccountApplication({stale.account_id: _Projection(stale)}),
    )

    snapshot = portfolio.rebuild()

    assert snapshot.freshness is PortfolioFreshness.STALE
    with pytest.raises(RuntimeError, match="is not current"):
        portfolio.require_current()


def test_portfolio_records_earn_holdings_without_treating_them_as_positions() -> None:
    account = _account(
        "main",
        SPOT,
        generation=3,
        equity="1000",
        balance="100",
        quantity="2",
        side=PositionSide.LONG,
    )
    segment = account.segments[0]
    account = replace(
        account,
        segments=(
            replace(
                segment,
                earn_holdings=(
                    EarnHolding(
                        account.account_id,
                        segment.segment_key,
                        "position:earn-1",
                        "USDT001",
                        "USDT",
                        Decimal("50"),
                        Decimal("50"),
                        EarnHoldingState.ACTIVE,
                        EarnLiquidity.IMMEDIATE,
                        participant_position_id="earn-1",
                        observed_at_unix_nanos=300,
                    ),
                ),
                earn_watermark_unix_nanos=300,
            ),
        ),
    )
    portfolio = PortfolioApplication(
        "paper:strategy-a",
        AccountApplication({account.account_id: _Projection(account)}),
    )

    snapshot = portfolio.rebuild()

    assert len(snapshot.earn_holdings) == 1
    assert snapshot.earn_holdings[0].principal == Decimal("50")
    assert snapshot.holding("USDT001") is None


def test_market_observation_advances_portfolio_valuation_watermark() -> None:
    account = _account(
        "main",
        SPOT,
        generation=1,
        equity="100",
        balance="100",
        quantity="1",
        side=PositionSide.LONG,
    )
    portfolio = PortfolioApplication(
        "paper:strategy-a",
        AccountApplication({account.account_id: _Projection(account)}),
    )
    occurred_at = datetime(2026, 8, 19, tzinfo=timezone.utc)
    event = QuoteEvent(
        Quote(
            scope=ObservationScope.market(MarketId("market:test:BTCUSDT")),
            instrument=InstrumentRef(
                InstrumentId("instrument:test:BTCUSDT"), "BTCUSDT"
            ),
            bid_price=Decimal("99"),
            bid_quantity=Decimal("1"),
            ask_price=Decimal("101"),
            ask_quantity=Decimal("1"),
            occurred_at=occurred_at,
            occurred_at_unix_nanos=1_787_097_600_000_000_000,
        ),
        EventMetadata(
            "market.events",
            9,
            producer="market",
            occurred_at=occurred_at,
            occurred_at_unix_nanos=1_787_097_600_000_000_000,
        ),
    )

    snapshot = portfolio.observe(event)

    assert snapshot.portfolio_version == 1
    assert snapshot.valuation_watermark is not None
    assert snapshot.valuation_watermark.stream_id == "market.events"
    assert snapshot.valuation_watermark.sequence == 9
