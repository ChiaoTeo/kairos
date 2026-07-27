from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Protocol, TYPE_CHECKING

from kairospy.context import DataContext
from kairospy.intents import IntentJournal, TradeIntent, target_position_intent
from kairospy.strategy.control import ControlFactory, ControlJournal
from kairospy.strategy.views import ViewStore

if TYPE_CHECKING:
    from kairospy.runtime.components import AccountCurrentView
    from kairospy.runtime.events import ClockEvent
    from kairospy.runtime.events import MarketEvent
    from kairospy.runtime.market import MarketSubscription
    from kairospy.schema import Quote


StrategyOutput = object | None


@dataclass(frozen=True, slots=True)
class StrategyContext:
    data: DataContext
    event: "MarketEvent | None" = None
    clock: "ClockEvent | None" = None
    state: Mapping[str, object] = field(default_factory=dict)
    intents: IntentJournal = field(default_factory=IntentJournal)
    controls: ControlJournal = field(default_factory=ControlJournal)
    views: ViewStore = field(default_factory=ViewStore)
    strategy_id: str = "strategy"
    phase: str = "idle"
    market: Any = None
    _subscriptions: Any = None
    _requests: Any = None
    _emitted_intents: list[TradeIntent] = field(default_factory=list, repr=False, compare=False)

    @property
    def now(self):
        if self.event is not None:
            return self.event.time
        if self.clock is not None:
            return self.clock.time
        return None

    @property
    def stream(self) -> str | None:
        return self.event.stream if self.event is not None else None

    @property
    def control(self) -> ControlFactory:
        return ControlFactory(strategy_id=self.strategy_id, requested_at=self.now, journal=self.controls)

    def view(self, key: str, default: object = None) -> object:
        return self.views.get(key, default)

    def account(self, key: str | None = None) -> "AccountCurrentView":
        """Return the current account view published by the active runtime."""
        if key is not None:
            return self.views.require(key)
        account_keys = tuple(
            view_key
            for view_key in self.views.envelopes()
            if view_key.startswith("account.current.")
        )
        if not account_keys:
            raise KeyError("no account view is available")
        if len(account_keys) > 1:
            raise ValueError("multiple account views are available; pass an account view key")
        return self.views.require(account_keys[0])

    def subscribe_quote(
        self,
        instrument: object,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> "MarketSubscription":
        if self._subscriptions is None:
            raise RuntimeError("runtime has no market subscription registry")
        resolved = self.data.markets.resolve(instrument, venue=venue, market=market)
        return self._subscriptions.subscribe_quote(resolved)

    def unsubscribe(self, subscription: "MarketSubscription | str") -> None:
        if self._subscriptions is None:
            raise RuntimeError("runtime has no market subscription registry")
        self._subscriptions.unsubscribe(subscription)

    def unsubscribe_quote(
        self,
        instrument: object,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> None:
        if self._subscriptions is None:
            raise RuntimeError("runtime has no market subscription registry")
        resolved = self.data.markets.resolve(instrument, venue=venue, market=market)
        self._subscriptions.unsubscribe(f"market.quote.{resolved.market_key}")

    def request_quote(
        self,
        instrument: object,
        *,
        venue: str | None = None,
        market: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> "Quote | None":
        if self._requests is None:
            raise RuntimeError("runtime has no market request service")
        return self._requests.request_quote(instrument, venue=venue, market=market, params=params)

    def target_position(
        self,
        instrument: object,
        quantity: Decimal | str | int | float,
        *,
        limit_price: Decimal | str | int | float | None = None,
        reason: str = "",
        intent_id: str | None = None,
    ) -> TradeIntent:
        resolved = self.data.markets.resolve(instrument)
        intent = target_position_intent(
            strategy_id=self.strategy_id,
            instrument_id=resolved.instrument_id,
            market_id=resolved.market_id,
            target_quantity=Decimal(str(quantity)),
            at=self.now,
            limit_price=None if limit_price is None else Decimal(str(limit_price)),
            reason=reason,
            intent_id=intent_id,
        )
        occurred_at = self.now or datetime.now(timezone.utc)
        self.intents.record_intent(intent, at=occurred_at)
        self._emitted_intents.append(intent)
        return intent


class Strategy(Protocol):
    @property
    def strategy_id(self) -> str:
        ...

    def on_start(self, context: StrategyContext) -> StrategyOutput:
        ...

    def on_market(self, context: StrategyContext, event: "MarketEvent") -> StrategyOutput:
        ...

    def on_clock(self, context: StrategyContext, event: "ClockEvent") -> StrategyOutput:
        ...

    def on_end(self, context: StrategyContext) -> StrategyOutput:
        ...


class StrategyBase:
    strategy_id = "strategy"

    def on_start(self, context: StrategyContext) -> StrategyOutput:
        return ()

    def on_market(self, context: StrategyContext, event: "MarketEvent") -> StrategyOutput:
        return ()

    def on_clock(self, context: StrategyContext, event: "ClockEvent") -> StrategyOutput:
        return ()

    def on_end(self, context: StrategyContext) -> StrategyOutput:
        return ()


Context = StrategyContext


__all__ = ["Context", "ControlFactory", "Strategy", "StrategyBase", "StrategyContext", "StrategyOutput"]
