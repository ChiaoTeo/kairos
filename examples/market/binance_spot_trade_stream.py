"""Listen to Binance Spot trades through an Integration connection.

This is the smallest public-market example. It intentionally does not create
a usecase runtime; use ``binance_spot_runtime.py`` for that layer.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from kairospy.application.support.composition.application.integrations import connect_binance_spot
from kairospy.domain.market import TradePrint
from kairospy.domain.reference import MarketRef
from kairospy.infrastructure.integrations.application.connections import ConnectionMarketSubscriptionRequest, RuntimeMode


async def listen(symbol: str, limit: int) -> None:
    assembly = connect_binance_spot("example.binance.spot.public", market=True, mode=RuntimeMode.LIVE)
    access = assembly.capabilities.public_market
    if access is None:
        raise RuntimeError("Binance connection did not provide public market capability")
    remote = await access.subscribe(
        ConnectionMarketSubscriptionRequest(
            market=MarketRef.ephemeral(venue="binance", market="spot", source_symbol=symbol),
            selector=TradePrint,
            identity=f"example.binance.trade.{symbol.lower()}",
        )
    )
    count = 0
    try:
        async for event in remote.events():
            count += 1
            print(f"{count:>4} {event.observed_at.isoformat()} {event.value.market_key} price={event.value.price} size={event.value.size}")
            if count >= limit:
                break
    finally:
        await access.unsubscribe(remote.subscription_id)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--events", type=int, default=10)
    args = parser.parse_args(argv)
    if args.events < 1:
        raise SystemExit("--events must be positive")
    asyncio.run(listen(args.symbol, args.events))


if __name__ == "__main__":
    main()
