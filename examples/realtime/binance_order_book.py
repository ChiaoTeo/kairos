"""Build a valid live Binance book from REST Snapshot + WebSocket Delta."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from kairospy.ports import Environment
from kairospy.connectors.binance.market_stream import BinanceStreamSession, WebSocketClientConnector, websocket_url
from kairospy.connectors.binance.order_book import (
    BinanceOrderBookSnapshotProvider, BinanceOrderBookSyncService,
)
from kairospy.connectors.binance.rest_transport import UrllibBinanceTransport
from kairospy.connectors.binance.stream import BinanceCanonicalStreamService
from kairospy.trading.identity import InstrumentId
from kairospy.market_data import (
    BoundedEventChannel, CanonicalCaptureWriter, CanonicalOrderBookProjection,
    CapturedCanonicalEventSource,
)
from kairospy.storage.codec import to_primitive


async def capture(args) -> dict[str, object]:
    symbol = args.symbol.upper()
    instrument = InstrumentId(f"crypto:binance:spot:{symbol}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    root = args.output / f"{symbol.lower()}-depth-{stamp}"
    raw_path = root.with_suffix(".raw.jsonl")
    aligned_path = root.with_suffix(".aligned.canonical.jsonl")
    raw_channel = BoundedEventChannel(max(128, args.messages * 2))
    aligned_channel = BoundedEventChannel(max(128, args.messages * 2))
    stream_id = f"{symbol.lower()}@depth@100ms"
    stream = BinanceCanonicalStreamService(
        BinanceStreamSession(
            WebSocketClientConnector(timeout=15),
            websocket_url(Environment.LIVE, stream_id, public_only=True), journal=raw_path,
        ),
        {symbol: instrument}, raw_channel,
        source_instance="example-depth-raw", stream_id=stream_id,
    )
    sync = BinanceOrderBookSyncService(
        raw_channel,
        BinanceOrderBookSnapshotProvider(
            UrllibBinanceTransport("https://data-api.binance.vision", timeout=15),
            symbol, instrument, limit=args.depth,
        ),
        aligned_channel,
        source_instance="example-depth-aligned", stream_id=stream_id,
        canonical_capture=CanonicalCaptureWriter(
            aligned_path, session_id=root.name, source="binance",
        ),
    )
    observed = []
    live = CanonicalOrderBookProjection()

    async def consume_aligned() -> None:
        async for event in aligned_channel.events():
            observed.append(event)
            state = live.apply(event)
            if args.print_events and state is not None:
                print(json.dumps({
                    "available_time": event.available_time.isoformat(),
                    "kind": event.kind.value,
                    "sequence": state.sequence,
                    "best_bid": str(state.best_bid),
                    "best_ask": str(state.best_ask),
                    "valid": state.valid,
                    "flags": event.flags,
                }, sort_keys=True), file=sys.stderr, flush=True)

    await asyncio.gather(
        stream.run(message_limit=args.messages), sync.run(), consume_aligned(),
    )
    replayed = [event async for event in CapturedCanonicalEventSource(aligned_path).events()]
    replay = CanonicalOrderBookProjection()
    for event in replayed:
        replay.apply(event)
    state = live.get(instrument)
    if not state.valid or state != replay.get(instrument):
        raise RuntimeError("aligned order book is invalid or replay diverged")
    return {
        "symbol": symbol, "raw_deltas": stream.canonical_events,
        "aligned_events": len(observed), "sequence": state.sequence,
        "best_bid": str(state.best_bid), "best_ask": str(state.best_ask),
        "book_valid": state.valid, "live_equals_replay": True,
        "sync_metrics": to_primitive(sync.metrics),
        "raw_journal": str(raw_path), "aligned_capture": str(aligned_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--messages", type=int, default=25)
    parser.add_argument("--depth", type=int, choices=(5, 10, 20, 50, 100, 500, 1000), default=100)
    parser.add_argument(
        "--print-events", action="store_true",
        help="print each snapshot-aligned best bid/ask to stderr as soon as it is strategy-safe",
    )
    parser.add_argument("--output", type=Path, default=Path("example-output/binance-depth"))
    args = parser.parse_args()
    if args.messages < 1:
        raise SystemExit("--messages must be positive")
    print(json.dumps(to_primitive(asyncio.run(capture(args))), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
