"""Discover and print Binance BTC option quotes without placing orders."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from kairospy.application.support.composition.application.integrations import connect_binance_options
from kairospy.application.actor.support.services.connections import IntegrationConnectionScope
from kairospy.application.usecases.market.application.runtime import build_live_market
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.domain.subscriptions import MarketDataSubscriptionSpec
from kairospy.domain.market import Quote
from kairospy.infrastructure.integrations.application.connections import RuntimeMode
from kairospy.infrastructure.integrations.domain import TransportKind


class StopAfter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0

    def should_stop(self) -> bool:
        return self.count >= self.limit


async def listen(*, symbol: str | None, events: int, poll_seconds: float) -> None:
    connection = connect_binance_options(
        "example.binance.options",
        transport=TransportKind.MARKET_STREAM,
        mode=RuntimeMode.LIVE,
    )
    contracts = connection.contracts(underlying="BTC")  # type: ignore[attr-defined]
    selected = next((item for item in contracts if str(item.market.source_symbol) == symbol), None) if symbol else (sorted(contracts, key=lambda item: item.expiry)[0] if contracts else None)
    if selected is None:
        available = ", ".join(str(item.market.source_symbol) for item in contracts[:10])
        raise RuntimeError(f"no Binance BTC option found; available sample: {available or 'none'}")
    market = selected.market
    connections = IntegrationConnectionScope()
    data = build_live_market(
        source_name="binance-options-example",
        connections=connections,
        stream_connections={"binance-options": connection},
    )
    data.set_market_service(MarketApplication())
    data.subscribe(MarketDataSubscriptionSpec(market, (Quote,), identity="example.binance.options.quote", params={"poll_seconds": poll_seconds}))
    stop = StopAfter(events)
    data.set_stop_signal(stop)
    print(f"contract={selected.market.source_symbol} expiry={selected.expiry.isoformat()} strike={selected.strike} right={selected.right}", flush=True)
    try:
        async for envelope in data.events():
            stop.count += 1
            quote = envelope.payload.value
            print(f"{stop.count:>4} symbol={selected.market.source_symbol} bid={quote.bid} ask={quote.ask} time={quote.time.isoformat()}", flush=True)
    finally:
        await connection.stop()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="Binance option symbol, for example BTC-260925-60000-C")
    parser.add_argument("--events", type=int, default=5)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.events < 1:
        raise SystemExit("--events must be positive")
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be positive")
    asyncio.run(listen(symbol=args.symbol, events=args.events, poll_seconds=args.poll_seconds))


if __name__ == "__main__":
    main()
