"""Python implementation of the Execution v2 cross-process contract.

The package mirrors Market: control, event decoding, and mmap view decoding
are separate contract concerns. Generated FlatBuffers objects are returned
directly; application models remain in ``kairospy.application.execution``.
"""

from __future__ import annotations

from typing import Any

from .control import ExecutionControlClient
from .events import decode_event
from .current import ExecutionCurrentViews
from .view import (
    ExecutionViewFrame,
    ExecutionViewKey,
    ExecutionViewKind,
    ExecutionViewReader,
    decode_view,
)


def backtest_market_payload(event: object) -> dict[str, Any] | None:
    """Map one strategy-visible Market event into Execution control payload."""

    from kairospy.application.market import BarEvent, QuoteEvent

    if isinstance(event, QuoteEvent):
        quote = event.data
        if quote.market_id is None:
            raise ValueError(
                "Execution backtest input requires a market-scoped quote; "
                "resolve consolidated data through an Execution destination route first"
            )
        if quote.provider is None:
            raise ValueError("Execution backtest quote requires provider provenance")
        body: dict[str, object] = {
            "Quote": {
                "scope": {"kind": "market", "market_id": str(quote.market_id)},
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
                "provider": quote.provider,
            }
        }
    elif isinstance(event, BarEvent):
        bar = event.data
        if bar.market_id is None:
            raise ValueError(
                "Execution backtest input requires a market-scoped bar; "
                "resolve consolidated data through an Execution destination route first"
            )
        if bar.provider is None:
            raise ValueError("Execution backtest bar requires provider provenance")
        body = {
            "Bar": {
                "scope": {"kind": "market", "market_id": str(bar.market_id)},
                "instrument_id": str(bar.instrument.id),
                "timeframe": bar.timeframe,
                "open": format(bar.open, "f"),
                "high": format(bar.high, "f"),
                "low": format(bar.low, "f"),
                "close": format(bar.close, "f"),
                "volume": None if bar.volume is None else format(bar.volume, "f"),
                "observed_at_unix_nanos": bar.occurred_at_unix_nanos,
                "provider": bar.provider,
                "derivation": "provider",
            }
        }
    else:
        return None
    return body


__all__ = [
    "ExecutionControlClient",
    "ExecutionViewFrame",
    "ExecutionViewKey",
    "ExecutionViewKind",
    "ExecutionViewReader",
    "ExecutionCurrentViews",
    "decode_event",
    "decode_view",
    "backtest_market_payload",
]
