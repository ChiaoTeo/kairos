"""Python-facing Market contract.

This module is the only Market process-boundary entry point used by the
strategy runtime.  The transport package remains an implementation detail;
callers receive the typed snapshot/event and command adapters from here.
"""

from __future__ import annotations

from pathlib import Path

from kairospy.infrastructure.transport.commands import (
    MarketUnixCommandPort,
    UnixJsonCommandClient,
)
from kairospy.infrastructure.transport.market import (
    BarView,
    DecimalValue,
    EventStreamGap,
    GreeksView,
    MarketDataView,
    OrderBookView,
    PriceLevelView,
    MmapMarketSnapshotReader,
    QuoteView,
    TradeView,
    UnixMarketEventStream,
)

from .base import CommandEnvelope, QueryEnvelope


def snapshot_reader(path: str | Path) -> MmapMarketSnapshotReader:
    return MmapMarketSnapshotReader(path)


def event_stream(
    path: str | Path,
    *,
    stream_id: str = "market.events",
    replayable: bool = False,
) -> UnixMarketEventStream:
    return UnixMarketEventStream(path, stream_id=stream_id, replayable=replayable)


def command_port(
    path: str | Path, *, launch_id: str | None = None
) -> MarketUnixCommandPort:
    return MarketUnixCommandPort(UnixJsonCommandClient(path), launch_id=launch_id)


__all__ = [
    "BarView",
    "CommandEnvelope",
    "DecimalValue",
    "EventStreamGap",
    "GreeksView",
    "MarketDataView",
    "OrderBookView",
    "PriceLevelView",
    "MmapMarketSnapshotReader",
    "QueryEnvelope",
    "QuoteView",
    "TradeView",
    "UnixMarketEventStream",
    "command_port",
    "event_stream",
    "snapshot_reader",
]
