"""Listen to Massive real-time AAPL quotes through the Market application."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from kairospy.application.support.composition.application.integrations import connect_massive_stocks
from kairospy.application.usecases.market.application.runtime import build_live_market
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.domain.subscriptions import MarketDataSubscriptionSpec
from kairospy.domain.market import Quote
from kairospy.domain.reference import MarketRef
from kairospy.infrastructure.integrations.application.connections import RuntimeMode


class StopAfter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0

    def should_stop(self) -> bool:
        return self.count >= self.limit


async def listen(symbol: str, limit: int) -> None:
    connection = connect_massive_stocks(
        "example.massive.stocks", credential="massive_stocks", mode=RuntimeMode.LIVE
    )
    market = MarketRef.ephemeral(venue="massive", market="equity", source_symbol=symbol)
    application = MarketApplication()
    data = build_live_market(
        source_name="massive-stocks-example",
        market_service=application,
        stream_connections={"massive": connection},
    )
    data.subscribe(MarketDataSubscriptionSpec(market, (Quote,), identity=f"example.massive.{symbol.lower()}"))
    stop = StopAfter(limit)
    data.set_stop_signal(stop)

    async for envelope in data.events():
        stop.count += 1
        quote = envelope.payload.value
        print(
            f"{stop.count:>4} {quote.market_key} "
            f"bid={quote.bid} x {quote.bid_size} "
            f"ask={quote.ask} x {quote.ask_size} "
            f"mid={quote.midpoint}",
            flush=True,
        )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--events", type=int, default=10)
    args = parser.parse_args(argv)
    if args.events < 1:
        raise SystemExit("--events must be positive")
    try:
        asyncio.run(listen(args.symbol, args.events))
    except RuntimeError as error:
        raise SystemExit(f"Massive market feed unavailable: {error}") from error


if __name__ == "__main__":
    main()
