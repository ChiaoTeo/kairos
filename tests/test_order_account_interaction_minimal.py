from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from kairospy.core.account import (
    AccountBalance,
    AccountContext,
    AccountRef,
    AccountSnapshot,
    AccountSource,
    Environment,
    MarginScope,
    MarginState,
    OpenOrderSnapshot,
    ReservationStatus,
    project_account,
)
from kairospy.core.execution import FillReport, ExecutionCoordinator
from kairospy.service.domains.execution import JsonExecutionStateStore
from kairospy.integrations.payloads import CcxtAccountBootstrapParser
from kairospy.core.reference import MarketResolver
from kairospy.core.execution import ExecutionUpdate
from kairospy.integrations.payloads.ccxt_execution import ccxt_order_update, ingest_ccxt_order_update
from kairospy.core.order import OrderEvent, OrderEventKind, OrderOrigin, OrderRequest, OrderSide, OrderStatus
from kairospy.service.domains.account import bootstrap_account


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeBroker:
    def __init__(self):
        self.created = []
        self.canceled = []

    def create_order(self, symbol, *, side, type, amount, price=None, params=None):
        self.created.append((symbol, side, type, amount, price, dict(params or {})))
        return {
            "id": "venue-1",
            "symbol": symbol,
            "side": side,
            "type": type,
            "amount": str(amount),
            "price": str(price),
            "params": params,
        }

    def cancel_order(self, id, *, symbol=None, params=None):
        self.canceled.append((id, symbol, dict(params or {})))
        return {"id": id, "symbol": symbol, "status": "canceled"}


class FakeBootstrapBroker:
    def fetch_balance(self, *, params=None):
        assert params == {"type": "spot"}
        return {
            "free": {"USDT": "900"},
            "used": {"USDT": "100"},
            "total": {"USDT": "1000"},
        }

    def fetch_open_orders(self, symbol=None, *, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        assert params == {"type": "spot"}
        return (
            {
                "id": "venue-existing-1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "type": "limit",
                "amount": "1",
                "filled": "0.25",
                "remaining": "0.75",
                "price": "100",
                "cost": "75",
            },
        )


def _context() -> AccountContext:
    return AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)


def _snapshot(context: AccountContext, free: str = "100", locked: str = "0") -> AccountSnapshot:
    return AccountSnapshot(
        context,
        balances=(
            AccountBalance.from_free_locked(
                "USDT",
                Decimal(free),
                Decimal(locked),
                source=AccountSource.VENUE,
            ),
        ),
        observed_at=NOW,
    )


def _margin_snapshot(context: AccountContext, available: str) -> AccountSnapshot:
    return AccountSnapshot(
        context,
        balances=(
            AccountBalance.from_free_locked(
                "USDT",
                Decimal("100"),
                Decimal("0"),
                source=AccountSource.VENUE,
            ),
        ),
        margins=(
            MarginState(
                "USDT",
                Decimal("10"),
                Decimal("5"),
                AccountSource.VENUE,
                scope=MarginScope.INSTRUMENT,
                instrument_id="BTC/USDT",
                available=Decimal(available),
            ),
        ),
        observed_at=NOW,
    )


def test_order_state_machine_allows_cancel_requested_order_to_fill() -> None:
    context = _context()
    state = OrderRequest("client-1", context, "BTC/USDT", OrderSide.BUY, Decimal("1"))
    coordinator = ExecutionCoordinator(broker=FakeBroker())

    coordinator.plan_order(state, at=NOW)
    coordinator.submit_order("client-1", at=NOW)
    coordinator.request_cancel("client-1", at=NOW)
    filled = coordinator.ingest_fill(
        FillReport(
            "client-1",
            NOW,
            Decimal("1"),
            Decimal("100"),
            "USDT",
            cash_delta=Decimal("-100"),
        )
    )

    assert filled.status is OrderStatus.FILLED
    assert filled.venue_order_id == "venue-1"


def test_coordinator_cancel_order_calls_broker_and_releases_reservation() -> None:
    context = _context()
    broker = FakeBroker()
    coordinator = ExecutionCoordinator(broker=broker)
    request = OrderRequest("client-1", context, "BTC/USDT", OrderSide.BUY, Decimal("1"))
    coordinator.plan_order(
        request,
        reserve_currency="USDT",
        reserve_amount=Decimal("100"),
        venue_snapshot=_snapshot(context, free="100"),
        at=NOW,
    )
    coordinator.submit_order("client-1", at=NOW)

    state = coordinator.cancel_order("client-1", at=NOW, params={"recvWindow": 5000})

    assert broker.canceled == [("venue-1", "BTC/USDT", {"recvWindow": 5000})]
    assert state.status is OrderStatus.CANCELED
    assert coordinator.reservations.reservations[0].status is ReservationStatus.RELEASED


def test_coordinator_maps_canonical_instrument_id_to_broker_symbol_for_submit_and_cancel() -> None:
    context = _context()
    broker = FakeBroker()
    coordinator = ExecutionCoordinator(
        broker=broker,
        broker_symbol_resolver=lambda instrument_id: {
            "binance_spot_btc_usdt": "BTC/USDT",
        }[instrument_id],
    )
    request = OrderRequest("client-1", context, "binance_spot_btc_usdt", OrderSide.BUY, Decimal("1"))
    coordinator.plan_order(request, at=NOW)

    coordinator.submit_order("client-1", at=NOW, params={"timeInForce": "GTC"})
    coordinator.cancel_order("client-1", at=NOW, params={"recvWindow": 5000})

    assert broker.created == [
        ("BTC/USDT", "buy", "market", Decimal("1"), None, {"timeInForce": "GTC"}),
    ]
    assert broker.canceled == [("venue-1", "BTC/USDT", {"recvWindow": 5000})]


def test_coordinator_margin_plan_reserves_required_margin_or_rejects() -> None:
    context = _context()
    accepted = ExecutionCoordinator()
    accepted_request = OrderRequest("client-1", context, "BTC/USDT", OrderSide.BUY, Decimal("1"))

    accepted_state = accepted.plan_order(
        accepted_request,
        reserve_currency="USDT",
        margin_notional=Decimal("200"),
        margin_leverage=Decimal("5"),
        venue_snapshot=_margin_snapshot(context, "50"),
        at=NOW,
    )

    rejected = ExecutionCoordinator()
    rejected_request = OrderRequest("client-2", context, "BTC/USDT", OrderSide.BUY, Decimal("1"))
    rejected_state = rejected.plan_order(
        rejected_request,
        reserve_currency="USDT",
        margin_notional=Decimal("300"),
        margin_leverage=Decimal("5"),
        venue_snapshot=_margin_snapshot(context, "50"),
        at=NOW,
    )

    assert accepted_state.status is OrderStatus.RESERVED
    assert accepted.reservations.reservations[0].amount == Decimal("40")
    assert rejected_state.status is OrderStatus.REJECTED
    assert rejected_state.reason == "insufficient available margin"


def test_json_execution_state_store_restores_orders_ledger_and_reservations(tmp_path: Path) -> None:
    context = _context()
    coordinator = ExecutionCoordinator(broker=FakeBroker())
    request = OrderRequest("client-1", context, "BTC/USDT", OrderSide.BUY, Decimal("1"))
    coordinator.plan_order(
        request,
        reserve_currency="USDT",
        reserve_amount=Decimal("100"),
        venue_snapshot=_snapshot(context, free="100"),
        at=NOW,
    )
    coordinator.submit_order("client-1", at=NOW)
    coordinator.ingest_fill(
        FillReport(
            "client-1",
            NOW,
            Decimal("1"),
            Decimal("100"),
            "USDT",
            cash_delta=Decimal("-100"),
            fee_currency="USDT",
            fee_amount=Decimal("0.1"),
        )
    )
    store = JsonExecutionStateStore(tmp_path / "execution-state.json")

    store.save(coordinator).restore_into(restored := ExecutionCoordinator())

    assert restored.orders.get("client-1").status is OrderStatus.FILLED
    assert restored.ledger.cash(context.account) == {"USDT": Decimal("-100.1")}
    assert restored.ledger.positions(context.account) == {"BTC/USDT": Decimal("1")}
    assert restored.reservations.reservations[0].status is ReservationStatus.CONSUMED


def test_coordinator_keeps_orders_and_account_state_separate_but_composes_projection() -> None:
    context = _context()
    coordinator = ExecutionCoordinator()
    request = OrderRequest("client-1", context, "BTC/USDT", OrderSide.BUY, Decimal("1"))

    state = coordinator.plan_order(
        request,
        reserve_currency="USDT",
        reserve_amount=Decimal("25"),
        venue_snapshot=_snapshot(context, free="100"),
        at=NOW,
    )
    projection = project_account(
        context,
        venue=_snapshot(context, free="100"),
        reservations=coordinator.reservations,
    )

    assert state.status is OrderStatus.RESERVED
    assert projection.balance("USDT").total == Decimal("100")
    assert projection.balance("USDT").free == Decimal("75")
    assert coordinator.ledger.events == ()


def test_reflected_reservation_stops_local_double_counting_after_venue_snapshot_catches_up() -> None:
    context = _context()
    coordinator = ExecutionCoordinator()
    request = OrderRequest("client-1", context, "BTC/USDT", OrderSide.BUY, Decimal("1"))
    coordinator.plan_order(
        request,
        reserve_currency="USDT",
        reserve_amount=Decimal("25"),
        venue_snapshot=_snapshot(context, free="100"),
        at=NOW,
    )

    coordinator.mark_reservation_reflected("client-1")
    projection = project_account(
        context,
        venue=_snapshot(context, free="75", locked="25"),
        reservations=coordinator.reservations,
    )

    assert coordinator.reservations.reservations[0].status is ReservationStatus.REFLECTED
    assert projection.balance("USDT").free == Decimal("75")
    assert projection.balance("USDT").locked == Decimal("25")


def test_fill_writes_account_ledger_and_consumes_reservation() -> None:
    context = _context()
    coordinator = ExecutionCoordinator(broker=FakeBroker())
    request = OrderRequest("client-1", context, "BTC/USDT", OrderSide.BUY, Decimal("1"))
    coordinator.plan_order(
        request,
        reserve_currency="USDT",
        reserve_amount=Decimal("100"),
        venue_snapshot=_snapshot(context, free="100"),
        at=NOW,
    )
    coordinator.submit_order("client-1", at=NOW)

    state = coordinator.ingest_fill(
        FillReport(
            "client-1",
            NOW,
            Decimal("1"),
            Decimal("100"),
            "USDT",
            cash_delta=Decimal("-100"),
            fee_currency="USDT",
            fee_amount=Decimal("0.1"),
        )
    )

    assert state.status is OrderStatus.FILLED
    assert coordinator.reservations.reservations[0].status is ReservationStatus.CONSUMED
    assert [event.cash_delta for event in coordinator.ledger.events] == [Decimal("-100"), Decimal("-0.1")]


def test_order_book_records_events_without_mutating_account_projection_by_itself() -> None:
    context = _context()
    coordinator = ExecutionCoordinator()
    request = OrderRequest("client-1", context, "BTC/USDT", OrderSide.BUY, Decimal("1"))

    coordinator.orders.plan(request)
    coordinator.orders.record(OrderEvent("client-1", OrderEventKind.SUBMITTED, NOW))
    projection = project_account(context, venue=_snapshot(context, free="100"))

    assert coordinator.orders.get("client-1").status is OrderStatus.SUBMITTING
    assert projection.balance("USDT").free == Decimal("100")


def test_external_venue_order_gets_stable_local_identity_without_system_client_id() -> None:
    context = _context()
    coordinator = ExecutionCoordinator()

    state = coordinator.orders.import_venue_open_order(
        context=context,
        venue_order_id="venue-existing-1",
        instrument_id="BTC/USDT",
        side=OrderSide.SELL,
        quantity=Decimal("0.5"),
        limit_price=Decimal("50000"),
        observed_at=NOW,
    )

    assert state.request.origin is OrderOrigin.VENUE
    assert state.identity.local_order_id == "external:binance:main:spot:venue-existing-1"
    assert state.identity.client_order_id is None
    assert state.identity.venue_order_id == "venue-existing-1"
    assert state.status is OrderStatus.ACKNOWLEDGED


def test_venue_order_id_can_match_external_order_events() -> None:
    context = _context()
    coordinator = ExecutionCoordinator()
    imported = coordinator.orders.import_venue_open_order(
        context=context,
        venue_order_id="venue-existing-1",
        instrument_id="BTC/USDT",
        side=OrderSide.SELL,
        quantity=Decimal("0.5"),
        limit_price=Decimal("50000"),
        observed_at=NOW,
    )

    canceled = coordinator.orders.record(
        OrderEvent("venue-existing-1", OrderEventKind.CANCELED, NOW, venue_order_id="venue-existing-1")
    )

    assert canceled.local_order_id == imported.local_order_id
    assert canceled.status is OrderStatus.CANCELED


def test_account_projection_exposes_venue_open_orders_and_local_pending_orders() -> None:
    context = _context()
    coordinator = ExecutionCoordinator()
    request = OrderRequest("client-1", context, "ETH/USDT", OrderSide.BUY, Decimal("2"))
    coordinator.plan_order(request, at=NOW)
    venue = AccountSnapshot(
        context,
        balances=(AccountBalance.from_free_locked("USDT", Decimal("100"), Decimal("0"), source=AccountSource.VENUE),),
        open_orders=(
            OpenOrderSnapshot(
                "venue-existing-1",
                "BTC/USDT",
                "sell",
                Decimal("0.5"),
                AccountSource.VENUE,
                reserved_currency="BTC",
                reserved_amount=Decimal("0.5"),
            ),
        ),
        observed_at=NOW,
    )

    projection = coordinator.account_projection(context, venue_snapshot=venue)

    assert [order.order_id for order in projection.open_orders] == ["venue-existing-1"]
    assert [order.local_order_id for order in projection.pending_orders] == ["client-1"]
    assert projection.source is AccountSource.MIXED


def test_bootstrap_account_imports_ccxt_balance_and_existing_open_orders() -> None:
    context = _context()
    coordinator = ExecutionCoordinator()

    result = bootstrap_account(
        context,
        FakeBootstrapBroker(),
        coordinator,
        CcxtAccountBootstrapParser(),
        symbol="BTC/USDT",
        at=NOW,
        balance_params={"type": "spot"},
        order_params={"type": "spot"},
    )

    assert result.snapshot.balances[0].total == Decimal("1000")
    assert result.snapshot.balances[0].free == Decimal("900")
    assert result.snapshot.balances[0].locked == Decimal("100")
    assert [order.order_id for order in result.snapshot.open_orders] == ["venue-existing-1"]
    assert [order.local_order_id for order in result.imported_orders] == ["external:binance:main:spot:venue-existing-1"]
    assert result.imported_orders[0].status is OrderStatus.PARTIALLY_FILLED
    assert coordinator.orders.get_by_venue_order_id("venue-existing-1").remaining_quantity == Decimal("0.75")
    assert [order.local_order_id for order in result.projection.pending_orders] == [
        "external:binance:main:spot:venue-existing-1"
    ]


def test_ccxt_account_payload_adapter_parses_account_and_instrument_margins() -> None:
    context = _context()
    snapshot = CcxtAccountBootstrapParser().snapshot(
        context,
        {
            "free": {"USDT": "900"},
            "used": {"USDT": "100"},
            "total": {"USDT": "1000"},
            "info": {
                "marginAsset": "USDT",
                "totalInitialMargin": "120",
                "totalMaintMargin": "60",
                "availableBalance": "800",
            },
            "positions": [
                {
                    "symbol": "BTC/USDT",
                    "marginAsset": "USDT",
                    "initialMargin": "40",
                    "maintenanceMargin": "20",
                    "availableMargin": "50",
                }
            ],
        },
        (),
        observed_at=NOW,
    )

    assert [
        (item.currency, item.initial, item.maintenance, item.scope, item.instrument_id, item.available)
        for item in snapshot.margins
    ] == [
        ("USDT", Decimal("120"), Decimal("60"), MarginScope.ACCOUNT, None, Decimal("800")),
        ("USDT", Decimal("40"), Decimal("20"), MarginScope.INSTRUMENT, "instrument:unknown:btc:usdt", Decimal("50")),
    ]


def test_ccxt_account_payload_adapter_uses_market_resolver_for_instrument_identity() -> None:
    context = _context()
    adapter = CcxtAccountBootstrapParser(MarketResolver(default_venue="binance", default_market="spot"))

    snapshot = adapter.snapshot(
        context,
        {
            "free": {"USDT": "900"},
            "used": {"USDT": "100"},
            "total": {"USDT": "1000"},
            "positions": [
                {
                    "symbol": "BTC/USDT",
                    "marginAsset": "USDT",
                    "initialMargin": "40",
                    "maintenanceMargin": "20",
                    "availableMargin": "50",
                }
            ],
        },
        (
            {
                "id": "venue-1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "type": "limit",
                "amount": "1",
                "remaining": "1",
                "price": "100",
                "cost": "100",
            },
        ),
        observed_at=NOW,
    )

    assert snapshot.margins[0].instrument_id == "instrument:spot:btc:usdt"
    assert snapshot.open_orders[0].instrument_id == "instrument:spot:btc:usdt"

    coordinator = ExecutionCoordinator()
    state = adapter.import_open_order(
        context,
        coordinator,
        {
            "id": "venue-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": "1",
            "remaining": "1",
            "price": "100",
        },
        observed_at=NOW,
    )

    assert state.request.instrument_id == "instrument:spot:btc:usdt"
    assert state.request.market_id == "market:binance:spot:btc_usdt"


def test_ccxt_order_ws_update_imports_unknown_active_external_order() -> None:
    context = _context()
    coordinator = ExecutionCoordinator()

    state = ingest_ccxt_order_update(
        coordinator,
        context,
        {
            "id": "venue-ws-1",
            "symbol": "BTC/USDT",
            "side": "sell",
            "type": "limit",
            "amount": "1",
            "filled": "0.2",
            "remaining": "0.8",
            "price": "100",
            "status": "open",
            "timestamp": 1767225600000,
        },
    )

    assert state.local_order_id == "external:binance:main:spot:venue-ws-1"
    assert state.status is OrderStatus.PARTIALLY_FILLED
    assert state.remaining_quantity == Decimal("0.8")


def test_ccxt_order_payload_adapter_emits_core_execution_update() -> None:
    update = ccxt_order_update(
        _context(),
        {
            "id": "venue-ws-1",
            "symbol": "BTC/USDT",
            "side": "sell",
            "type": "limit",
            "amount": "1",
            "filled": "0.2",
            "remaining": "0.8",
            "price": "100",
            "status": "open",
            "timestamp": 1767225600000,
        },
    )

    assert isinstance(update, ExecutionUpdate)
    assert update.venue_order_id == "venue-ws-1"
    assert update.instrument_id == "instrument:unknown:btc:usdt"
    assert update.filled_quantity == Decimal("0.2")
    assert update.remaining_quantity == Decimal("0.8")


def test_ccxt_order_ws_update_advances_known_system_order_by_venue_id() -> None:
    context = _context()
    coordinator = ExecutionCoordinator(broker=FakeBroker())
    request = OrderRequest("client-1", context, "BTC/USDT", OrderSide.BUY, Decimal("1"))
    coordinator.plan_order(request, at=NOW)
    coordinator.submit_order("client-1", at=NOW)

    partial = ingest_ccxt_order_update(
        coordinator,
        context,
        {
            "id": "venue-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "market",
            "amount": "1",
            "filled": "0.25",
            "remaining": "0.75",
            "status": "open",
            "timestamp": 1767225600000,
        },
    )
    filled = ingest_ccxt_order_update(
        coordinator,
        context,
        {
            "id": "venue-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "market",
            "amount": "1",
            "filled": "1",
            "remaining": "0",
            "status": "closed",
            "timestamp": 1767225660000,
        },
    )

    assert partial.status is OrderStatus.PARTIALLY_FILLED
    assert filled.status is OrderStatus.FILLED
    assert filled.filled_quantity == Decimal("1")
