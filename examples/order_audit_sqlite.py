"""Run a local order-stream audit example without a venue connection.

Usage:
    uv run python examples/order_audit_sqlite.py --reset
    uv run kairos order trace --db .kairos/examples/order-audit.sqlite --order-id example-order-1
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from kairospy.application.usecases.execution.application.component import (
    ExecutionApplication,
    PlanOrderCommand,
    SubmitOrderCommand,
)
from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator
from kairospy.domain.account import (
    AccountBalance,
    AccountRuntimeContext,
    AccountSegment,
    AccountSnapshot,
    AccountSource,
    Environment,
    OpenOrderSnapshot,
)
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.order import OrderRequest, OrderSide, OrderType
from kairospy.infrastructure.persistence.services.execution.sqlite_audit import SqliteOrderAuditStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path(".kairos/examples/order-audit.sqlite"))
    parser.add_argument("--instance-id", default="example-instance-a")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    if args.reset and args.db.exists():
        args.db.unlink()

    instance_id = args.instance_id
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = AccountRuntimeContext(AccountSegment("demo", "main", product_family="spot"), Environment.PAPER)
    store = SqliteOrderAuditStore(args.db, instance_id=instance_id)
    coordinator = ExecutionCoordinator(audit_store=store, instance_id=instance_id)
    execution = ExecutionApplication.compose(coordinator, audit_store=store, instance_id=instance_id)
    snapshot = AccountSnapshot(
        context,
        (AccountBalance.from_free_locked("USDT", Decimal("200"), Decimal("0"), source=AccountSource.VENUE),),
        observed_at=at,
    )
    request = OrderRequest(
        "example-order-1",
        context,
        "BTCUSDT",
        OrderSide.BUY,
        Decimal("1"),
        OrderType.LIMIT,
        Decimal("100"),
    )
    execution.plan_order(
        PlanOrderCommand(
            request,
            at,
            reserve_currency="USDT",
            reserve_amount=Decimal("100"),
            venue_snapshot=snapshot,
        )
    )
    execution.submit_order(SubmitOrderCommand(request.order_id, at + timedelta(milliseconds=10)))
    acknowledged = ExecutionUpdate(
        at + timedelta(milliseconds=20),
        "acknowledged",
        order_id=request.order_id,
        order_venue_id="venue-order-1",
        context=context,
        metadata={"event_id": "venue-event-1"},
        source="example-venue",
    )
    execution.apply_update(acknowledged)
    execution.apply_update(acknowledged)  # duplicate receipt is retained in the audit journal
    execution.reflect_account_snapshot(
        AccountSnapshot(
            context,
            snapshot.balances,
            open_orders=(OpenOrderSnapshot("venue-order-1", "BTCUSDT", "buy", Decimal("1"), AccountSource.VENUE, "USDT", Decimal("100")),),
            observed_at=at + timedelta(milliseconds=30),
        )
    )
    state = execution.orders()[0]
    rows = store.trace(request.order_id)
    print(f"database={args.db}")
    print(f"order={state.order_id} status={state.status.value} transitions={len(rows)}")
    print("next:")
    print(f"  uv run kairos order trace --db {args.db} --order-id {request.order_id}")
    print(f"  uv run kairos order events --db {args.db} --account {context.segment.account_id} --format jsonl")


if __name__ == "__main__":
    main()
