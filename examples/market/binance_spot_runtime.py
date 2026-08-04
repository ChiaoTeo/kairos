"""Consume Binance Spot trades through the Market usecase runtime service."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from kairospy.application.support.composition.application.integrations import market_integration_runtime
from kairospy.application.usecases.market.application.runtime import build_live_market
from kairospy.application.system.core.application.connections import IntegrationConnectionScope
from kairospy.application.usecases.market.domain.subscriptions import MarketDataSubscriptionSpec
from kairospy.domain.market import TradePrint
from kairospy.domain.reference import MarketRef
from kairospy.infrastructure.integrations.application.connections import RuntimeMode


class StopAfter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0

    def should_stop(self) -> bool:
        return self.count >= self.limit


async def listen(symbol: str, limit: int) -> None:
    connections = IntegrationConnectionScope()
    integration_runtime = market_integration_runtime(connections, mode=RuntimeMode.LIVE)
    market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol=symbol)
    data = build_live_market(
        source_name="binance-spot-example",
        connections=connections,
        integration_runtime=integration_runtime,
    )
    data.subscribe(MarketDataSubscriptionSpec(market=market, selectors=(TradePrint,), identity=f"example.market.runtime.{symbol.lower()}"))
    stop = StopAfter(limit)
    data.set_stop_signal(stop)

    async for envelope in data.events():
        stop.count += 1
        value = envelope.payload.value
        print(f"{stop.count:>4} envelope={envelope.kind} market={value.market_key} price={value.price} size={value.size}")


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
