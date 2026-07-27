from __future__ import annotations

import argparse
from datetime import datetime
import json
from typing import Iterable, Mapping

from kairospy.context import DataContext
from kairospy.reference import MarketRef
from kairospy.data import DataStore
from kairospy.integrations import CcxtDriver, Hyperliquid
from kairospy.runtime import ClockEvent, IterableEventSource, StrategyRuntime
from kairospy.strategy import StrategyBase, StrategyContext


class ClockQuoteStrategy(StrategyBase):
    strategy_id = "hyperliquid-clock-quote"

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def on_start(self, context: StrategyContext):
        subscription = context.subscribe_quote(self.symbol, venue="hyperliquid", market="derivative")
        return ({
            "type": "subscribed",
            "subscription": subscription.key,
            "instrument_id": subscription.instrument_id,
        },)

    def on_market(self, context: StrategyContext, event):
        quote = context.market.latest_quote(self.symbol, venue="hyperliquid", market="derivative")
        return ({
            "type": "stream_quote",
            "time": event.time.isoformat(),
            "bid": str(quote.bid) if quote and quote.bid is not None else None,
            "ask": str(quote.ask) if quote and quote.ask is not None else None,
            "midpoint": str(quote.midpoint) if quote and quote.midpoint is not None else None,
        },)

    def on_clock(self, context: StrategyContext, event: ClockEvent):
        quote = context.request_quote(self.symbol, venue="hyperliquid", market="derivative")
        latest = context.market.latest_quote(self.symbol, venue="hyperliquid", market="derivative")
        return ({
            "type": "clock_quote",
            "clock": event.time.isoformat(),
            "bid": str(quote.bid) if quote and quote.bid is not None else None,
            "ask": str(quote.ask) if quote and quote.ask is not None else None,
            "latest_matches_request": quote == latest,
        },)


class FixtureHyperliquidQuoteProvider:
    def fetch_quote(self, market: MarketRef, *, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        return {
            "time": "2026-01-01T00:01:00+00:00",
            "source": "hyperliquid.fixture",
            "bid": "100.5",
            "ask": "101.5",
            "bid_size": "2",
            "ask_size": "3",
        }


class MixedEventSource:
    def __init__(self, events: Iterable[object]) -> None:
        self._events = tuple(events)

    def events(self):
        return iter(self._events)


def main() -> None:
    args = _parse_args()
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    provider = Hyperliquid(CcxtDriver()) if args.live else FixtureHyperliquidQuoteProvider()
    stream_event = next(IterableEventSource("hyperliquid.quote." + args.symbol, [_fixture_stream_row(args.symbol)]).events())
    source = MixedEventSource((
        stream_event,
        ClockEvent(datetime.fromisoformat(args.clock_time)),
    ))

    result = StrategyRuntime(
        ClockQuoteStrategy(args.symbol),
        data,
        quote_provider=provider,
    ).run(source)

    print(json.dumps({
        "strategy_id": result.strategy_id,
        "callbacks": [record.hook for record in result.callbacks],
        "intents": list(result.intents),
    }, indent=2, sort_keys=True))


def _fixture_stream_row(symbol: str) -> dict[str, object]:
    market = MarketRef.ephemeral(venue="hyperliquid", market="derivative", source_symbol=symbol)
    return {
        "time": "2026-01-01T00:00:00+00:00",
        "kind": "quote",
        **market.identity_fields(),
        "bid1": "100",
        "ask1": "101",
        "bid1_size": "1",
        "ask1_size": "1.5",
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Demonstrate runtime-owned Hyperliquid quote subscription and clock-only REST quote requests."
    )
    parser.add_argument("--symbol", default="BTC/USDC:USDC")
    parser.add_argument("--clock-time", default="2026-01-01T00:01:00+00:00")
    parser.add_argument("--live", action="store_true", help="Use Hyperliquid through ccxt instead of fixture data.")
    return parser.parse_args()


if __name__ == "__main__":
    main()
