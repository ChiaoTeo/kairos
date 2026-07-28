from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from kairospy.core.account import (
    AccountBalance,
    AccountContext,
    AccountEvent,
    AccountEventKind,
    AccountLedger,
    AccountRef,
    AccountSnapshot,
    AccountSource,
    Environment,
    MarginScope,
    MarginState,
    OpenOrderSnapshot,
    PositionSnapshot,
    derive_account_state,
)
from kairospy.core.execution import (
    CashBuyingPowerModel,
    MarginBuyingPowerModel,
    Reservation,
    ReservationBook,
    reserve_cash_order,
)
from kairospy.core.order import OrderEvent, OrderEventKind, OrderJournal, OrderRequest, OrderSide
from kairospy.application.service.domains.account import compare_account_state


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _account() -> AccountRef:
    return AccountRef("binance", "main", "um_futures")


def _context() -> AccountContext:
    return AccountContext(_account(), Environment.LIVE)


def test_account_balance_requires_consistent_total_free_and_locked() -> None:
    with pytest.raises(ValueError, match="total == free"):
        AccountBalance("USDT", Decimal("100"), Decimal("80"), Decimal("10"), AccountSource.VENUE)

    balance = AccountBalance.from_total_locked(
        "USDT",
        Decimal("100"),
        Decimal("25"),
        source=AccountSource.VENUE,
    )

    assert balance.free == Decimal("75")
    assert balance.locked == Decimal("25")


def test_live_projection_keeps_venue_snapshot_authoritative_and_adds_local_reservations() -> None:
    context = _context()
    account = context.account
    snapshot = AccountSnapshot(
        context,
        balances=(
            AccountBalance.from_free_locked(
                "USDT",
                Decimal("900"),
                Decimal("100"),
                source=AccountSource.VENUE,
            ),
        ),
        margins=(
            MarginState(
                "USDT",
                Decimal("50"),
                Decimal("25"),
                AccountSource.VENUE,
                scope=MarginScope.INSTRUMENT,
                instrument_id="BTC/USDT:PERP",
                available=Decimal("850"),
            ),
        ),
        positions=(
            PositionSnapshot(
                "BTC/USDT:PERP",
                Decimal("0.2"),
                AccountSource.VENUE,
                average_price=Decimal("40000"),
            ),
        ),
        observed_at=NOW,
    )
    reservations = ReservationBook()
    reservation = Reservation(
        "client-order-1",
        account,
        "USDT",
        Decimal("75"),
        "pre-submit local order hold",
        NOW,
        order_id="client-order-1",
    )

    check = reserve_cash_order(
        reservations,
        reservation,
        derive_account_state(context, venue=snapshot),
    )
    projection = derive_account_state(context, venue=snapshot, holds=reservations)

    assert check.accepted
    assert projection.source is AccountSource.MIXED
    assert projection.balance("USDT").total == Decimal("1000")
    assert projection.balance("USDT").free == Decimal("825")
    assert projection.balance("USDT").locked == Decimal("175")
    assert projection.margins[0].source is AccountSource.VENUE
    assert projection.positions[0].source is AccountSource.VENUE


def test_ledger_only_projection_models_backtest_or_simulation_account_state() -> None:
    account = AccountRef("simulated", "strategy-a")
    context = AccountContext(account, Environment.BACKTEST)
    ledger = AccountLedger((
        AccountEvent(
            uuid4(),
            account,
            AccountEventKind.DEPOSIT,
            NOW,
            "USD",
            cash_delta=Decimal("1000"),
        ),
        AccountEvent(
            uuid4(),
            account,
            AccountEventKind.FILL,
            NOW,
            "USD",
            cash_delta=Decimal("-100"),
            instrument_id="AAPL",
            position_delta=Decimal("1"),
        ),
    ))

    projection = derive_account_state(context, ledger=ledger)

    assert projection.source is AccountSource.LEDGER
    assert projection.balance("USD").total == Decimal("900")
    assert projection.balance("USD").free == Decimal("900")
    assert projection.positions == (
        PositionSnapshot("AAPL", Decimal("1"), AccountSource.LEDGER),
    )


def test_local_cash_model_rejects_more_than_free_balance() -> None:
    context = _context()
    projection = derive_account_state(
        context,
        venue=AccountSnapshot(
            context,
            balances=(
                AccountBalance.from_free_locked(
                    "USDT",
                    Decimal("10"),
                    Decimal("90"),
                    source=AccountSource.VENUE,
                ),
            ),
            observed_at=NOW,
        ),
    )

    check = CashBuyingPowerModel().check(projection, currency="USDT", notional=Decimal("11"))

    assert not check.accepted
    assert check.available == Decimal("10")


def test_local_margin_model_uses_instrument_available_margin_and_leverage() -> None:
    context = _context()
    projection = derive_account_state(
        context,
        venue=AccountSnapshot(
            context,
            balances=(AccountBalance.from_free_locked("USDT", Decimal("100"), Decimal("0"), source=AccountSource.VENUE),),
            margins=(
                MarginState(
                    "USDT",
                    Decimal("20"),
                    Decimal("10"),
                    AccountSource.VENUE,
                    scope=MarginScope.INSTRUMENT,
                    instrument_id="BTC/USDT",
                    available=Decimal("50"),
                ),
            ),
            observed_at=NOW,
        ),
    )

    accepted = MarginBuyingPowerModel().check(
        projection,
        currency="USDT",
        instrument_id="BTC/USDT",
        notional=Decimal("200"),
        leverage=Decimal("5"),
    )
    rejected = MarginBuyingPowerModel().check(
        projection,
        currency="USDT",
        instrument_id="BTC/USDT",
        notional=Decimal("300"),
        leverage=Decimal("5"),
    )

    assert accepted.accepted
    assert accepted.requested == Decimal("40")
    assert not rejected.accepted
    assert rejected.requested == Decimal("60")
    assert rejected.available == Decimal("50")


def test_compare_account_state_reports_balance_fields_separately() -> None:
    context = _context()
    local = derive_account_state(
        context,
        venue=AccountSnapshot(
            context,
            balances=(AccountBalance.from_free_locked("USDT", Decimal("90"), Decimal("10"), source=AccountSource.VENUE),),
            observed_at=NOW,
        ),
    )
    external = AccountSnapshot(
        context,
        balances=(AccountBalance.from_free_locked("USDT", Decimal("80"), Decimal("20"), source=AccountSource.VENUE),),
        observed_at=NOW,
    )

    differences = compare_account_state(local, external)

    assert [(item.kind, item.key, item.local, item.external) for item in differences] == [
        ("balance.free", "USDT", Decimal("90"), Decimal("80")),
        ("balance.locked", "USDT", Decimal("10"), Decimal("20")),
    ]


def test_compare_account_state_reports_open_order_presence_and_quantity_differences() -> None:
    context = _context()
    local = derive_account_state(
        context,
        venue=AccountSnapshot(
            context,
            balances=(AccountBalance.from_free_locked("USDT", Decimal("100"), Decimal("0"), source=AccountSource.VENUE),),
            open_orders=(
                OpenOrderSnapshot("venue-local-only", "BTC/USDT", "buy", Decimal("1"), AccountSource.VENUE),
                OpenOrderSnapshot("venue-size-mismatch", "ETH/USDT", "sell", Decimal("2"), AccountSource.VENUE),
            ),
            observed_at=NOW,
        ),
    )
    external = AccountSnapshot(
        context,
        balances=(AccountBalance.from_free_locked("USDT", Decimal("100"), Decimal("0"), source=AccountSource.VENUE),),
        open_orders=(
            OpenOrderSnapshot("venue-external-only", "SOL/USDT", "buy", Decimal("3"), AccountSource.VENUE),
            OpenOrderSnapshot("venue-size-mismatch", "ETH/USDT", "sell", Decimal("1.5"), AccountSource.VENUE),
        ),
        observed_at=NOW,
    )

    differences = compare_account_state(local, external)

    assert [(item.kind, item.key, item.local, item.external) for item in differences] == [
        ("open_order.present", "venue-external-only", Decimal("0"), Decimal("1")),
        ("open_order.present", "venue-local-only", Decimal("1"), Decimal("0")),
        ("open_order.quantity", "venue-size-mismatch", Decimal("2"), Decimal("1.5")),
    ]


def test_compare_account_state_reports_pending_order_missing_or_remaining_mismatch() -> None:
    context = _context()
    missing = OrderRequest("client-missing", context, "BTC/USDT", OrderSide.BUY, Decimal("1"))
    mismatch = OrderRequest("client-mismatch", context, "ETH/USDT", OrderSide.SELL, Decimal("2"))
    journal = OrderJournal()
    journal.plan(missing)
    journal.record(OrderEvent("client-missing", OrderEventKind.SUBMITTED, NOW))
    journal.record(OrderEvent("client-missing", OrderEventKind.ACKNOWLEDGED, NOW, venue_order_id="venue-missing"))
    journal.plan(mismatch)
    journal.record(OrderEvent("client-mismatch", OrderEventKind.SUBMITTED, NOW))
    journal.record(OrderEvent("client-mismatch", OrderEventKind.ACKNOWLEDGED, NOW, venue_order_id="venue-mismatch"))
    local = derive_account_state(
        context,
        venue=AccountSnapshot(
            context,
            balances=(AccountBalance.from_free_locked("USDT", Decimal("100"), Decimal("0"), source=AccountSource.VENUE),),
            observed_at=NOW,
        ),
    )
    external = AccountSnapshot(
        context,
        balances=(AccountBalance.from_free_locked("USDT", Decimal("100"), Decimal("0"), source=AccountSource.VENUE),),
        open_orders=(
            OpenOrderSnapshot("venue-mismatch", "ETH/USDT", "sell", Decimal("1.25"), AccountSource.VENUE),
        ),
        observed_at=NOW,
    )

    differences = compare_account_state(local, external, pending_orders=journal.active_for_context(context))

    assert [(item.kind, item.key, item.local, item.external) for item in differences] == [
        ("open_order.present", "venue-mismatch", Decimal("0"), Decimal("1")),
        ("pending_order.remaining_quantity", "venue-mismatch", Decimal("2"), Decimal("1.25")),
        ("pending_order.venue_present", "venue-missing", Decimal("1"), Decimal("0")),
    ]
