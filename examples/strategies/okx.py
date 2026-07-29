from __future__ import annotations

from decimal import Decimal

from kairospy.application.strategy import StrategyContext
from kairospy.core.market import Quote
from kairospy.application.strategy import Signal, StrategyBase


class OneShotLong(StrategyBase):
    strategy_id = "okx-one-shot-long"

    def __init__(self, symbol: str = "okx:spot:BTC/USDT", quantity: Decimal = Decimal("0.001")) -> None:
        self.symbol = symbol
        self.quantity = quantity
        self.entered = False

    def on_start(self, context: StrategyContext):
        context.subscribe_market_data(self.symbol, selectors=(Quote.select("bid", "ask", basis="ticker"),))
        return None

    def on_data(self, context: StrategyContext, signal: Signal):
        if self.entered or not signal.changed("market", "ticker"):
            return None
        context.target_position(self.symbol, self.quantity, account=0, intent_id="enter")
        self.entered = True
        return None


class LiveLimitLong(StrategyBase):
    strategy_id = "okx-live-limit-long"

    def __init__(self, symbol: str = "okx:spot:BTC/USDT", quantity: Decimal = Decimal("0.001")) -> None:
        self.symbol = symbol
        self.quantity = quantity
        self.entered = False

    def on_start(self, context: StrategyContext):
        context.subscribe_market_data(self.symbol, selectors=(Quote.select("bid", "ask", basis="ticker"),))
        return None

    def on_data(self, context: StrategyContext, signal: Signal):
        if self.entered or not signal.changed("market", "ticker"):
            return None
        ask = _latest_field(context, "Quote.ask")
        if ask is None:
            return None
        context.target_position(
            self.symbol,
            self.quantity,
            account=0,
            limit_price=Decimal(str(ask)),
            intent_id="enter-live-limit",
        )
        self.entered = True
        return None


class SpotBalanceHold(StrategyBase):
    strategy_id = "okx-spot-balance-hold"

    def __init__(
        self,
        symbol: str = "okx:spot:BTC/USDT",
        quantity: Decimal = Decimal("0.001"),
        tolerance: Decimal = Decimal("0.00000001"),
    ) -> None:
        self.symbol = symbol
        self.quantity = quantity
        self.tolerance = tolerance
        self.checked = False

    def on_start(self, context: StrategyContext):
        context.subscribe_market_data(self.symbol, selectors=(Quote.select("bid", "ask", basis="ticker"),))
        return None

    def on_data(self, context: StrategyContext, signal: Signal):
        if self.checked or not signal.changed("market", "ticker"):
            return None
        ask = _latest_field(context, "Quote.ask")
        if ask is None:
            return None
        current = _spot_balance_total(context, _base_currency(self.symbol))
        missing = self.quantity - current
        self.checked = True
        if missing <= self.tolerance:
            return None
        context.target_position(
            self.symbol,
            missing,
            account=0,
            limit_price=Decimal(str(ask)),
            intent_id="hold-spot-balance",
        )
        return None


def _latest_field(context: StrategyContext, field: str) -> object | None:
    view = context.views.require("market.fields")
    for item in reversed(tuple(getattr(view, "fields", ()))):
        if getattr(item, "field", None) == field:
            return getattr(item, "value", None)
    return None


def _spot_balance_total(context: StrategyContext, currency: str) -> Decimal:
    try:
        account = context.account()
    except (KeyError, ValueError):
        return Decimal("0")
    for balance in getattr(account, "balances", ()):
        if getattr(balance, "currency", "") == currency:
            return Decimal(str(getattr(balance, "total", "0")))
    return Decimal("0")


def _base_currency(symbol: str) -> str:
    value = symbol.split(":", 2)[-1]
    return value.split("/", 1)[0].upper()
