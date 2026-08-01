from __future__ import annotations

import logging
from decimal import Decimal

from kairospy.application.strategy import StrategyBase
from kairospy.core.market import Quote
from kairospy.core.reference import MarketRef


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class BinanceEquityPaperStrategy(StrategyBase):
    strategy_id = "binance-equity-paper"

    def __init__(self, *, symbol: str = "AAPL", target_quantity: str = "1") -> None:
        self.market = MarketRef.ephemeral(venue="binance", market="equity", source_symbol=symbol)
        self.target_quantity = Decimal(target_quantity)
        if self.target_quantity <= 0:
            raise ValueError("target_quantity must be positive")
        self.entered = False

    def on_start(self, context) -> None:
        logger.info(
            "starting strategy symbol=%s target_quantity=%s market=%s",
            self.market.source_symbol,
            self.target_quantity,
            self.market,
        )
        context.subscribe(self.market, selectors=(Quote,), identity=self.strategy_id)

    def on_data(self, context, signal) -> None:
        quote = _quote(signal)
        if quote is None:
            logger.debug("ignored non-quote signal signal=%r", signal)
            return None
        logger.info(
            "received quote symbol=%s bid=%s ask=%s target_quantity=%s entered=%s",
            self.market.source_symbol,
            quote.bid,
            quote.ask,
            self.target_quantity,
            self.entered,
        )
        if quote.ask is None:
            logger.info("skipping entry symbol=%s reason=missing_ask", self.market.source_symbol)
            return None
        if self.entered:
            logger.info("skipping entry symbol=%s reason=already_entered", self.market.source_symbol)
            return None
        self.entered = True
        logger.info(
            "submitting target position symbol=%s ask=%s target_quantity=%s",
            self.market.source_symbol,
            quote.ask,
            self.target_quantity,
        )
        context.trace(
            "paper_entry",
            {
                "symbol": str(self.market.source_symbol),
                "ask": str(quote.ask),
                "target_quantity": str(self.target_quantity),
            },
        )
        context.target_position(
            self.market,
            self.target_quantity,
            reason=f"paper entry from Binance equity quote ask={quote.ask}",
        )
        return None


def _quote(signal) -> Quote | None:
    payload = getattr(signal, "payload", None)
    value = getattr(payload, "value", None)
    return value if isinstance(value, Quote) else None


def strategy(symbol: str = "AAPL", target_quantity: str = "1") -> BinanceEquityPaperStrategy:
    return BinanceEquityPaperStrategy(symbol=symbol, target_quantity=target_quantity)
