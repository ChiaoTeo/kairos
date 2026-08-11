"""Python-facing Execution contract."""

from __future__ import annotations

from pathlib import Path

from kairospy.infrastructure.transport.commands import (
    ExecutionIntentCommandPort,
    ExecutionIntentQueryPort,
    UnixJsonCommandClient,
)

from .base import CommandEnvelope, MmapSnapshotReader, QueryEnvelope


def snapshot_reader(path: str | Path) -> MmapSnapshotReader:
    from kairospy.infrastructure.transport.generated.kairos.execution.v1.OrdersSnapshot import (
        OrdersSnapshot,
    )

    return MmapSnapshotReader(path, file_identifier=b"PEO1", root_type=OrdersSnapshot)


def intent_port(
    path: str | Path,
    *,
    allow_trading: bool = True,
    max_order_notional=None,
    require_limit_orders: bool = False,
    launch_id: str | None = None,
) -> ExecutionIntentCommandPort:
    return ExecutionIntentCommandPort(
        UnixJsonCommandClient(path),
        allow_trading=allow_trading,
        max_order_notional=max_order_notional,
        require_limit_orders=require_limit_orders,
        launch_id=launch_id,
    )


def query_port(path: str | Path) -> ExecutionIntentQueryPort:
    return ExecutionIntentQueryPort(UnixJsonCommandClient(path))


def backtest_run(path: str | Path, request: dict) -> dict:
    """Run deterministic simulated execution against supplied market events."""
    status, value = UnixJsonCommandClient(path).request(
        "POST", "/v1/backtest/run", request
    )
    if status >= 400:
        raise RuntimeError(
            value.get("error", f"backtest run failed with status {status}")
        )
    return value


def backtest_market(path: str | Path, event) -> dict:
    """Forward one strategy-visible market event to simulated Execution."""
    from kairospy.infrastructure.transport.market import QuoteView

    if event.kind != "quote" or not isinstance(event.payload, QuoteView):
        return {"fills": []}
    quote = event.payload
    body = {
        "Quote": {
            "market_id": quote.market_id or "",
            "instrument_id": quote.instrument_id,
            "bid_price": None if quote.bid_price is None else quote.bid_price.value,
            "bid_quantity": None
            if quote.bid_quantity is None
            else quote.bid_quantity.value,
            "ask_price": None if quote.ask_price is None else quote.ask_price.value,
            "ask_quantity": None
            if quote.ask_quantity is None
            else quote.ask_quantity.value,
            "observed_at_unix_nanos": quote.event_time_unix_nanos,
            "source_id": quote.source_id or "strategy-market",
        }
    }
    status, value = UnixJsonCommandClient(path).request(
        "POST", "/v1/backtest/market", body
    )
    if status >= 400:
        raise RuntimeError(
            value.get("error", f"market simulation failed with status {status}")
        )
    return value


__all__ = [
    "CommandEnvelope",
    "ExecutionIntentCommandPort",
    "ExecutionIntentQueryPort",
    "QueryEnvelope",
    "backtest_market",
    "backtest_run",
    "intent_port",
    "query_port",
    "snapshot_reader",
]
