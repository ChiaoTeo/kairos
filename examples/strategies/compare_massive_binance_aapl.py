"""Compare the latest Massive and Binance Stocks AAPL quotes."""

from __future__ import annotations

from decimal import Decimal

from kairospy.application.usecases.strategy.protocol import StrategyBase
from kairospy.domain.market import Quote
from kairospy.domain.reference import MarketRef


class CompareMassiveBinanceAaplStrategy(StrategyBase):
    """A read-only strategy: subscribe to both feeds and print their spread."""

    strategy_id = "example-compare-massive-binance-aapl"

    def __init__(self) -> None:
        self.massive = MarketRef.ephemeral(venue="massive", market="equity", source_symbol="AAPL")
        self.binance = MarketRef.ephemeral(venue="binance", market="equity", source_symbol="AAPL")
        self._latest: dict[str, Quote] = {}

    def on_start(self, context) -> None:
        context.subscribe(self.massive, selectors=(Quote,), identity=self.strategy_id)
        context.subscribe(self.binance, selectors=(Quote,), identity=self.strategy_id)
        print("Subscribed to Massive and Binance Stocks AAPL quotes; waiting for both...", flush=True)

    def on_data(self, context, signal) -> None:
        event = signal.payload
        quote = getattr(event, "value", None)
        if not isinstance(quote, Quote) or quote.midpoint is None:
            return

        source = quote.source.lower()
        if source not in {"massive", "binance"}:
            return
        self._latest[source] = quote
        massive = self._latest.get("massive")
        binance = self._latest.get("binance")
        if massive is None or binance is None or massive.midpoint is None or binance.midpoint is None:
            return

        spread = massive.midpoint - binance.midpoint
        spread_pct = (spread / binance.midpoint * Decimal("100")) if binance.midpoint else None
        print(
            f"[{signal.time.isoformat()}] AAPL "
            f"massive_mid={massive.midpoint} binance_mid={binance.midpoint} "
            f"spread(massive-binance)={spread} spread_pct={spread_pct:.6f}%",
            flush=True,
        )


def strategy() -> CompareMassiveBinanceAaplStrategy:
    return CompareMassiveBinanceAaplStrategy()


__all__ = ["CompareMassiveBinanceAaplStrategy", "strategy"]
