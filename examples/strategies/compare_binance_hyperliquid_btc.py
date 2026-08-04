"""Compare Binance and Hyperliquid BTC/USDT spot quotes without orders."""

from __future__ import annotations

from decimal import Decimal

from kairospy.application.usecases.strategy.protocol import StrategyBase
from kairospy.domain.market import Quote
from kairospy.domain.reference import MarketRef


class CompareBinanceHyperliquidBtcStrategy(StrategyBase):
    strategy_id = "example-compare-binance-hyperliquid-btc"

    def __init__(self) -> None:
        self.binance = MarketRef.ephemeral(
            venue="binance", market="spot", source_symbol="BTC/USDT"
        )
        self.hyperliquid = MarketRef.ephemeral(
            venue="hyperliquid", market="spot", source_symbol="BTC/USDC"
        )
        self.latest: dict[str, Quote] = {}

    def on_start(self, context) -> None:
        context.subscribe(
            self.binance,
            selectors=(Quote,),
            identity=f"{self.strategy_id}.binance",
        )
        context.subscribe(
            self.hyperliquid,
            selectors=(Quote,),
            identity=f"{self.strategy_id}.hyperliquid",
        )
        print(
            "Subscribed to Binance BTC/USDT and Hyperliquid BTC/USDC spot quotes...",
            flush=True,
        )

    def on_data(self, context, signal) -> None:
        quote = getattr(getattr(signal, "payload", None), "value", None)
        if not isinstance(quote, Quote) or not quote.source:
            return

        self.latest[quote.source] = quote
        print(
            f"[{signal.time.isoformat()}] {quote.source} BTC "
            f"bid={quote.bid} x {quote.bid_size} "
            f"ask={quote.ask} x {quote.ask_size}",
            flush=True,
        )

        binance = self.latest.get("binance")
        hyperliquid = self.latest.get("hyperliquid")
        if binance is None or hyperliquid is None:
            return

        binance_mid = _midpoint(binance)
        hyperliquid_mid = _midpoint(hyperliquid)
        if binance_mid is None or hyperliquid_mid is None:
            return

        difference = binance_mid - hyperliquid_mid
        percentage = difference / hyperliquid_mid * Decimal("100") if hyperliquid_mid else None
        print(
            f"[{signal.time.isoformat()}] BTC "
            f"Binance mid={binance_mid} "
            f"Hyperliquid mid={hyperliquid_mid} "
            f"difference={difference} "
            f"difference_pct={percentage:.5f}%",
            flush=True,
        )
        context.trace(
            "btc_cross_exchange_quote",
            {
                "binance_mid": str(binance_mid),
                "hyperliquid_mid": str(hyperliquid_mid),
                "difference": str(difference),
                "difference_pct": None if percentage is None else str(percentage),
            },
        )


def _midpoint(quote: Quote) -> Decimal | None:
    if quote.bid is None or quote.ask is None:
        return None
    return (quote.bid + quote.ask) / Decimal("2")


def strategy() -> CompareBinanceHyperliquidBtcStrategy:
    return CompareBinanceHyperliquidBtcStrategy()


__all__ = ["CompareBinanceHyperliquidBtcStrategy", "strategy"]
