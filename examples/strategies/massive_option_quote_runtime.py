"""Run one strategy directly against Massive Options real-time quotes."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from kairospy.application.support.composition.application.integrations import connect_massive_options
from kairospy.application.usecases.market.application.runtime import build_live_market
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.domain.subscriptions import MarketDataSubscriptionSpec
from kairospy.infrastructure.integrations.application.connections import RuntimeMode

from massive_spy_option_quote import MassiveSpyOptionQuoteStrategy


class _StrategyContext:
    def __init__(self, data: object) -> None:
        self.data = data

    def subscribe(self, market, *, selectors, identity=None):
        return self.data.subscribe(
            MarketDataSubscriptionSpec(market, tuple(selectors), identity=identity)
        )


async def listen(contract: str, events: int) -> None:
    connection = connect_massive_options(
        "example.massive.options", credential="massive_stocks", mode=RuntimeMode.LIVE
    )
    data = build_live_market(
        source_name="massive-options-example",
        stream_connections={"massive": connection},
    )
    data.set_market_service(MarketApplication())
    try:
        strategy = MassiveSpyOptionQuoteStrategy(contract)
        strategy.on_start(_StrategyContext(data))

        count = 0
        async for signal in data.events():
            strategy.on_data(_StrategyContext(data), signal)
            count += 1
            if count >= events:
                break
    finally:
        await connection.stop()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default="O:SPXW260807P07360000")
    parser.add_argument("--events", type=int, default=10)
    args = parser.parse_args(argv)
    if args.events < 1:
        raise SystemExit("--events must be positive")
    asyncio.run(listen(args.contract, args.events))


if __name__ == "__main__":
    main()
