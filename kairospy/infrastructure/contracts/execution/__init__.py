"""Python implementation of the Execution v2 cross-process contract.

The package mirrors Market: control, event decoding, and mmap view decoding
are separate contract concerns. Generated FlatBuffers objects are returned
directly; application models remain in ``kairospy.application.execution``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kairospy.infrastructure.transport.commands import UnixJsonCommandClient

from .control import ExecutionControlClient
from .events import decode_event
from .projection import ExecutionProjection
from .view import (
    ExecutionViewFrame,
    ExecutionViewKey,
    ExecutionViewKind,
    ExecutionViewReader,
    decode_view,
)


def advance_time(path: str | Path, event_time_unix_nanos: int) -> dict[str, Any]:
    """Advance Execution's deterministic replay clock at an explicit barrier."""

    status, value = UnixJsonCommandClient(path).request(
        "POST",
        "/v1/time/advance",
        {"event_time_unix_nanos": int(event_time_unix_nanos)},
    )
    if status >= 400:
        raise RuntimeError(
            value.get("error", f"execution time advance failed with status {status}")
        )
    return value


def backtest_run(path: str | Path, request: dict[str, Any]) -> dict[str, Any]:
    status, value = UnixJsonCommandClient(path).request(
        "POST", "/v1/backtest/run", request
    )
    if status >= 400:
        raise RuntimeError(
            value.get("error", f"execution backtest failed with status {status}")
        )
    return value


def backtest_market(path: str | Path, event: object) -> dict[str, Any]:
    """Forward one strategy-visible Market event through Execution control."""

    from kairospy.application.market import BarEvent, QuoteEvent

    if isinstance(event, QuoteEvent):
        quote = event.data
        if quote.market_id is None:
            raise ValueError(
                "Execution backtest input requires a market-scoped quote; "
                "resolve consolidated data through an Execution destination route first"
            )
        body: dict[str, object] = {
            "Quote": {
                "market_id": str(quote.market_id),
                "instrument_id": str(quote.instrument.id),
                "bid_price": None
                if quote.bid_price is None
                else format(quote.bid_price, "f"),
                "bid_quantity": None
                if quote.bid_quantity is None
                else format(quote.bid_quantity, "f"),
                "ask_price": None
                if quote.ask_price is None
                else format(quote.ask_price, "f"),
                "ask_quantity": None
                if quote.ask_quantity is None
                else format(quote.ask_quantity, "f"),
                "observed_at_unix_nanos": quote.occurred_at_unix_nanos,
                "source_id": quote.source_id or "strategy-market",
            }
        }
    elif isinstance(event, BarEvent):
        bar = event.data
        if bar.market_id is None:
            raise ValueError(
                "Execution backtest input requires a market-scoped bar; "
                "resolve consolidated data through an Execution destination route first"
            )
        body = {
            "Bar": {
                "market_id": str(bar.market_id),
                "instrument_id": str(bar.instrument.id),
                "timeframe": bar.timeframe,
                "open": format(bar.open, "f"),
                "high": format(bar.high, "f"),
                "low": format(bar.low, "f"),
                "close": format(bar.close, "f"),
                "volume": None if bar.volume is None else format(bar.volume, "f"),
                "observed_at_unix_nanos": bar.occurred_at_unix_nanos,
                "source_id": bar.source_id or "strategy-market",
                "derivation": "provider",
            }
        }
    else:
        return {"fills": []}
    status, value = UnixJsonCommandClient(path).request(
        "POST", "/v1/backtest/market", body
    )
    if status >= 400:
        raise RuntimeError(
            value.get("error", f"execution market backtest failed with status {status}")
        )
    return value


__all__ = [
    "ExecutionControlClient",
    "ExecutionViewFrame",
    "ExecutionViewKey",
    "ExecutionViewKind",
    "ExecutionViewReader",
    "ExecutionProjection",
    "decode_event",
    "decode_view",
    "advance_time",
    "backtest_run",
    "backtest_market",
]
