from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterable, Mapping

from kairospy.integrations import Binance, CcxtDriver, Hyperliquid


async def main() -> None:
    args = _parse_args()
    exchange = _exchange(args.exchange)
    params: dict[str, object] = {
        "market": args.market,
        "max_events": args.limit,
        "poll_seconds": args.poll_seconds,
    }

    if args.kind == "ticker":
        rows = exchange.watch_ticker(args.symbol, params=params)
    elif args.kind == "orderbook":
        rows = exchange.watch_order_book(args.symbol, limit=args.depth, params=params)
    elif args.kind == "trades":
        rows = exchange.watch_trades(args.symbol, limit=args.trade_limit, params=params)
    else:
        raise ValueError(f"unsupported stream kind: {args.kind}")

    await _print_rows(rows, limit=args.limit)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print normalized market data stream rows.")
    parser.add_argument("--exchange", choices=["binance", "hyperliquid"], default="binance")
    parser.add_argument("--market", default="spot")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--kind", choices=["ticker", "orderbook", "trades"], default="orderbook")
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--trade-limit", type=int, default=10)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    return parser.parse_args()

def _exchange(name: str) -> Binance | Hyperliquid:
    driver = CcxtDriver()
    if name == "binance":
        return Binance(driver)
    if name == "hyperliquid":
        return Hyperliquid(driver)
    raise ValueError(f"unsupported exchange: {name}")


async def _print_rows(rows: AsyncIterable[Mapping[str, object]], *, limit: int) -> None:
    count = 0
    async for row in rows:
        print(json.dumps(dict(row), sort_keys=True, separators=(",", ":")))
        count += 1
        if count >= limit:
            break


if __name__ == "__main__":
    asyncio.run(main())
