from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterable, Mapping
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from typing import TextIO

from kairospy.context import DataContext
from kairospy.data import DataStore
from kairospy.integrations import CcxtDriver, Hyperliquid
from kairospy.runtime import MarketEvent, parse_event_time
from kairospy.strategy import StrategyBase, StrategyContext


class StreamMarketPrinter(StrategyBase):
    strategy_id = "hyperliquid-stream-printer"

    def on_start(self, context: StrategyContext):
        return ({"type": "strategy_started", "strategy_id": self.strategy_id},)

    def on_market(self, context: StrategyContext, event: MarketEvent):
        return ({
            "type": "market_data_seen",
            "stream": event.stream,
            "sequence": event.sequence,
            "time": event.time.isoformat(),
            "payload": event.payload,
        },)

    def on_end(self, context: StrategyContext):
        return ({
            "type": "strategy_finished",
            "strategy_id": self.strategy_id,
            "last_time": context.now.isoformat() if context.now else None,
        },)


async def run_stream_strategy(
    rows: AsyncIterable[Mapping[str, object]],
    *,
    stream: str,
    data: DataContext,
    strategy: StreamMarketPrinter,
    stdout: TextIO = sys.stdout,
    limit: int | None = None,
) -> int:
    sequence = 0
    last_event: MarketEvent | None = None

    _write_intents(strategy.on_start(StrategyContext(data)), stdout)
    try:
        async for row in rows:
            sequence += 1
            event = MarketEvent(
                stream=stream,
                sequence=sequence,
                time=_event_time(row),
                payload=row,
            )
            last_event = event
            _write_intents(strategy.on_market(StrategyContext(data, event=event), event), stdout)
            if limit is not None and sequence >= limit:
                break
    finally:
        _write_intents(strategy.on_end(StrategyContext(data, event=last_event)), stdout)

    return sequence


async def main() -> None:
    args = _parse_args()
    exchange = Hyperliquid(CcxtDriver())
    params = {
        "market": "derivative",
        "poll_seconds": args.poll_seconds,
    }
    if args.limit is not None:
        params["max_events"] = args.limit

    if args.kind == "ticker":
        rows = exchange.watch_ticker(args.symbol, params=params)
    elif args.kind == "orderbook":
        rows = exchange.watch_order_book(args.symbol, limit=args.depth, params=params)
    elif args.kind == "trades":
        rows = exchange.watch_trades(args.symbol, limit=args.trade_limit, params=params)
    else:
        raise ValueError(f"unsupported stream kind: {args.kind}")

    with TemporaryDirectory() as temporary:
        data = DataContext(DataStore(temporary, storage_format="jsonl"))
        await run_stream_strategy(
            rows,
            stream=f"hyperliquid.{args.kind}.{args.symbol}",
            data=data,
            strategy=StreamMarketPrinter(),
            limit=args.limit,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a strategy over Hyperliquid market data streams and print JSONL intents."
    )
    parser.add_argument("--symbol", default="BTC/USDC:USDC")
    parser.add_argument("--kind", choices=["ticker", "orderbook", "trades"], default="orderbook")
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--trade-limit", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None, help="Stop after N stream events. Omit to keep running.")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    return parser.parse_args()


def _event_time(row: Mapping[str, object]) -> datetime:
    if "time" not in row:
        return datetime.now(timezone.utc)
    return parse_event_time(row["time"])


def _write_intents(intents, stdout: TextIO) -> None:
    for intent in intents:
        print(json.dumps(intent, sort_keys=True, separators=(",", ":")), file=stdout, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
