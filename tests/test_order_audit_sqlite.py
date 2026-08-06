from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.usecases.execution.application.component import ExecutionApplication, PlanOrderCommand, SubmitOrderCommand
from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator
from kairospy.domain.account import AccountBalance, AccountRuntimeContext, AccountSegment, AccountSnapshot, AccountSource, Environment, OpenOrderSnapshot
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.order import OrderRequest, OrderSide, OrderType
from kairospy.infrastructure.persistence.services.execution.sqlite_audit import SqliteOrderAuditDirectory, SqliteOrderAuditStore


def test_sqlite_audit_directory_filters_across_instances(tmp_path) -> None:
    first = SqliteOrderAuditStore(tmp_path / "paper" / "instances" / "one" / "run.sqlite", instance_id="one")
    second = SqliteOrderAuditStore(tmp_path / "paper" / "instances" / "two" / "run.sqlite", instance_id="two")
    base = {
        "order_id": "shared-order",
        "account": "account-a",
        "broker": "broker-a",
        "exchange": "exchange-a",
        "product_type": "spot",
        "symbol": "BTCUSDT",
        "event_kind": "acknowledged",
        "outcome": "applied",
        "after_status": "acknowledged",
        "observed_at": "2026-01-01T00:00:00+00:00",
    }
    first.record_transition({**base, "record_id": "one-transition"})
    second.record_transition({**base, "record_id": "two-transition", "symbol": "ETHUSDT"})

    directory = SqliteOrderAuditDirectory(tmp_path)
    assert [row["instance_id"] for row in directory.events(account="account-a", symbol="BTCUSDT")] == ["one"]
    assert [row["instance_id"] for row in directory.events(order_id="shared-order")] == ["one", "two"]


def test_sqlite_audit_records_transitions_receipts_and_duplicates(tmp_path) -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = AccountRuntimeContext(AccountSegment("demo", "main", product_family="spot"), Environment.PAPER)
    store = SqliteOrderAuditStore(tmp_path / "run.sqlite", instance_id="instance-a")
    coordinator = ExecutionCoordinator(audit_store=store, instance_id="instance-a")
    app = ExecutionApplication.compose(coordinator, audit_store=store, instance_id="instance-a")
    request = OrderRequest("order-1", context, "BTCUSDT", OrderSide.BUY, Decimal("1"), OrderType.LIMIT, Decimal("100"))
    snapshot = AccountSnapshot(context, (AccountBalance.from_free_locked("USDT", Decimal("200"), Decimal("0"), source=AccountSource.VENUE),), observed_at=at)

    app.plan_order(PlanOrderCommand(request, at, reserve_currency="USDT", reserve_amount=Decimal("100"), venue_snapshot=snapshot))
    app.submit_order(SubmitOrderCommand(request.order_id, at))
    update = ExecutionUpdate(at, "acknowledged", order_id=request.order_id, order_venue_id="venue-1", context=context, metadata={"event_id": "event-1"})
    app.apply_update(update)
    app.apply_update(update)
    app.reflect_account_snapshot(
        AccountSnapshot(
            context,
            snapshot.balances,
            open_orders=(OpenOrderSnapshot("venue-1", "BTCUSDT", "buy", Decimal("1"), AccountSource.VENUE, "USDT", Decimal("100")),),
            observed_at=at,
        )
    )

    assert [row["after_status"] for row in store.trace(request.order_id)] == ["planned", "reserved", "submitting", "acknowledged", "reflected"]
    assert [row["outcome"] for row in store.events(order_id=request.order_id) if row["record_type"] == "receipt"] == ["received", "applied", "duplicate"]
    assert any(row["event_kind"] == "reservation_reflected" for row in store.trace(request.order_id))


def test_partial_fill_adjusts_unreflected_reservation(tmp_path) -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = AccountRuntimeContext(AccountSegment("demo", "main", product_family="spot"), Environment.PAPER)
    store = SqliteOrderAuditStore(tmp_path / "run.sqlite", instance_id="instance-a")
    coordinator = ExecutionCoordinator(audit_store=store, instance_id="instance-a")
    app = ExecutionApplication.compose(coordinator, audit_store=store, instance_id="instance-a")
    request = OrderRequest("order-partial", context, "BTCUSDT", OrderSide.BUY, Decimal("2"), OrderType.LIMIT, Decimal("100"))
    snapshot = AccountSnapshot(context, (AccountBalance.from_free_locked("USDT", Decimal("300"), Decimal("0"), source=AccountSource.VENUE),), observed_at=at)
    app.plan_order(PlanOrderCommand(request, at, reserve_currency="USDT", reserve_amount=Decimal("200"), venue_snapshot=snapshot))
    app.submit_order(SubmitOrderCommand(request.order_id, at))
    app.apply_update(ExecutionUpdate(at, "partially_filled", order_id=request.order_id, order_venue_id="venue-partial", context=context, quantity=Decimal("2"), filled_quantity=Decimal("1"), fill_quantity=Decimal("1"), fill_price=Decimal("100"), metadata={"event_id": "partial-1"}))
    assert coordinator.reservations.active_amounts(context.segment)["USDT"] == Decimal("100")
    assert any(row["event_kind"] == "reservation_adjusted" for row in store.trace(request.order_id))
