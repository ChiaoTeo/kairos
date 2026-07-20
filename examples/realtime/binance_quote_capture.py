"""Capture public Binance quotes and prove strategy replay parity."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples._support import MidpointTargetStrategy, example_context
from kairos.ports import Environment
from kairos.connectors.binance.market_stream import BinanceStreamSession, WebSocketClientConnector, websocket_url
from kairos.connectors.binance.stream import BinanceCanonicalStreamService
from kairos.domain.identity import InstrumentId
from kairos.market_data import (
    BoundedEventChannel, CanonicalCaptureWriter, CanonicalQuoteProjection,
    CapturedCanonicalEventSource, IterableEventSource,
)
from kairos.storage.codec import to_primitive
from kairos.strategies import CanonicalStrategyEventSession


async def capture(args) -> dict[str, object]:
    symbol = args.symbol.upper()
    instrument = InstrumentId(f"crypto:binance:spot:{symbol}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    root = args.output / f"{symbol.lower()}-{stamp}"
    raw_path = root.with_suffix(".raw.jsonl")
    canonical_path = root.with_suffix(".canonical.jsonl")
    output = BoundedEventChannel(max(32, args.messages * 2))
    service = BinanceCanonicalStreamService(
        BinanceStreamSession(
            WebSocketClientConnector(timeout=15),
            websocket_url(Environment.LIVE, f"{symbol.lower()}@bookTicker", public_only=True),
            journal=raw_path,
        ),
        {symbol: instrument}, output,
        source_instance="example-binance-quote", stream_id=f"{symbol.lower()}@bookTicker",
        canonical_capture=CanonicalCaptureWriter(
            canonical_path, session_id=root.name, source="binance",
        ),
    )
    producer = asyncio.create_task(service.run(message_limit=args.messages))
    observed = [event async for event in output.events()]
    await producer
    replayed = [event async for event in CapturedCanonicalEventSource(canonical_path).events()]

    live_quote, replay_quote = CanonicalQuoteProjection(), CanonicalQuoteProjection()
    for event in observed:
        live_quote.apply(event)
    for event in replayed:
        replay_quote.apply(event)
    threshold = Decimal(str(args.threshold))
    live_strategy = await CanonicalStrategyEventSession(
        IterableEventSource(observed), MidpointTargetStrategy(threshold), example_context,
    ).run()
    replay_strategy = await CanonicalStrategyEventSession(
        IterableEventSource(replayed), MidpointTargetStrategy(threshold), example_context,
    ).run()
    if observed != replayed or live_quote.get(instrument) != replay_quote.get(instrument):
        raise RuntimeError("quote capture replay diverged")
    if live_strategy != replay_strategy:
        raise RuntimeError("strategy live/replay audit diverged")
    state = live_quote.get(instrument)
    return {
        "symbol": symbol, "events": len(observed), "reconnects": service.reconnects,
        "raw_journal": str(raw_path), "canonical_capture": str(canonical_path),
        "best_bid": str(state.bid), "best_ask": str(state.ask), "midpoint": str(state.midpoint),
        "strategy_audit_hash": live_strategy.audit_hash,
        "projection_hash": live_strategy.projection_hash,
        "decision_hash": live_strategy.decision_hash,
        "intent_hash": live_strategy.intent_hash,
        "live_equals_replay": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--messages", type=int, default=10)
    parser.add_argument("--threshold", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--output", type=Path, default=Path("example-output/binance-quote"))
    args = parser.parse_args()
    if args.messages < 1:
        raise SystemExit("--messages must be positive")
    print(json.dumps(to_primitive(asyncio.run(capture(args))), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
