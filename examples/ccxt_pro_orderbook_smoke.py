from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kairospy.identity import InstrumentId
from kairospy.integrations.config import CcxtExchangeSettings
from kairospy.integrations.connectors.ccxt import CcxtOrderBookEventSource, CcxtSymbolMapper, build_ccxt_pro_exchange
from kairospy.integrations.connectors.ccxt.exchange_factory import normalized_ccxt_exchange_id
from kairospy.integrations.connectors.ccxt.market_stream import DEFAULT_CCXT_PRO_ORDER_BOOK_SYMBOLS


DEFAULT_EXCHANGES = ("hyperliquid",)

# "binance","okx"
async def main() -> None:
    args = _parser().parse_args()
    failures = []
    for exchange_id in args.exchange:
        try:
            await run_exchange(exchange_id, args.symbol, args.limit, args.depth, args.new_updates, args.timeout_ms)
        except Exception as error:
            failures.append((exchange_id, error))
            print(f"{exchange_id} failed: {type(error).__name__}: {error}")
            if args.fail_fast:
                raise
    if failures:
        raise SystemExit(1)


async def run_exchange(
    exchange_id: str,
    symbol: str | None,
    limit: int,
    depth: int | None,
    new_updates: bool,
    timeout_ms: int,
) -> None:
    normalized = normalized_ccxt_exchange_id(exchange_id)
    selected_symbol = symbol or DEFAULT_CCXT_PRO_ORDER_BOOK_SYMBOLS[normalized]
    settings = CcxtExchangeSettings(normalized, timeout_ms=timeout_ms, options=_default_options(normalized))
    exchange = build_ccxt_pro_exchange(settings)
    instrument_id = InstrumentId(f"crypto:{normalized}:ccxt:{selected_symbol}")
    source = CcxtOrderBookEventSource.for_instruments(
        exchange,
        provider=normalized,
        instrument_ids=(instrument_id,),
        symbol_mapper=CcxtSymbolMapper({instrument_id: selected_symbol}),
        depth=depth,
        new_updates=new_updates,
    )
    count = 0
    events = source.events()
    try:
        while count < limit:
            snapshot = await events.__anext__()
            ask = snapshot.asks[0] if snapshot.asks else None
            bid = snapshot.bids[0] if snapshot.bids else None
            print(f"{normalized} {selected_symbol} ask={ask} bid={bid} sequence={snapshot.sequence}")
            count += 1
    finally:
        await events.aclose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test CCXT Pro watch_order_book for supported crypto venues.")
    parser.add_argument(
        "--exchange",
        action="append",
        choices=("binance", "okx", "okex", "hyperliquid"),
        default=None,
        help="Exchange to test. Repeat for multiple exchanges. Defaults to binance, okx, hyperliquid.",
    )
    parser.add_argument("--symbol", help="Override symbol for every selected exchange.")
    parser.add_argument("--limit", type=int, default=3, help="Snapshots to print per exchange.")
    parser.add_argument("--depth", type=int, default=None, help="Optional order book depth/limit.")
    parser.add_argument("--timeout-ms", type=int, default=30000, help="CCXT REST/WS request timeout in milliseconds.")
    parser.add_argument("--new-updates", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--fail-fast", action="store_true", help="Stop on the first exchange failure.")
    original = parser.parse_args

    def parse_args(*args, **kwargs):
        namespace = original(*args, **kwargs)
        if namespace.exchange is None:
            namespace.exchange = list(DEFAULT_EXCHANGES)
        if namespace.limit < 1:
            parser.error("--limit must be positive")
        if namespace.timeout_ms < 1000:
            parser.error("--timeout-ms must be at least 1000")
        return namespace

    parser.parse_args = parse_args
    return parser


def _default_options(exchange_id: str) -> dict[str, object]:
    if exchange_id == "binance":
        return {"defaultType": "spot", "fetchMarkets": {"types": ["spot"]}}
    if exchange_id == "hyperliquid":
        return {"defaultType": "swap"}
    return {}


if __name__ == "__main__":
    asyncio.run(main())
