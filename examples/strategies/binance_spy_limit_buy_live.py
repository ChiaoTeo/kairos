"""Submit one Binance Stocks Trading SPY limit-buy intent on startup."""

from __future__ import annotations

from decimal import Decimal

from kairospy.application.usecases.strategy.protocol import StrategyBase
from kairospy.domain.reference import MarketRef


class BinanceSpyLimitBuyLiveStrategy(StrategyBase):
    strategy_id = "example-binance-spy-limit-buy-live"

    def __init__(
        self,
        *,
        account: str,
        segment: str = "spot",
        symbol: str = "SPY",
        quantity: str = "1",
        limit_price: str = "750",
    ) -> None:
        self.account = account
        self.segment = segment
        self.market = MarketRef.ephemeral(
            venue="binance",
            market="equity",
            source_symbol=symbol,
        )
        self.quantity = Decimal(quantity)
        self.limit_price = Decimal(limit_price)
        self.submitted = False

    def on_start(self, context) -> None:
        if self.submitted:
            return
        self.submitted = True
        context.target_position(
            self.market,
            self.quantity,
            account=self.account,
            account_segment=self.segment,
            limit_price=self.limit_price,
            reason="example Binance Stocks Trading SPY limit buy",
        )


def strategy(
    account: str = "binance_zhaoqian888666",
    segment: str = "spot",
    symbol: str = "SPY",
    quantity: str = "1",
    limit_price: str = "750",
) -> BinanceSpyLimitBuyLiveStrategy:
    return BinanceSpyLimitBuyLiveStrategy(
        account=account,
        segment=segment,
        symbol=symbol,
        quantity=quantity,
        limit_price=limit_price,
    )


__all__ = ["BinanceSpyLimitBuyLiveStrategy", "strategy"]
