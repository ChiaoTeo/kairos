from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence, TYPE_CHECKING

from kairospy.core.intent import IntentJournal, TradeIntent, target_position_intent
from kairospy.core.views import ViewStore

from .control import ControlFactory, ControlJournal
from .data import DataContext

if TYPE_CHECKING:
    from kairospy.application.runtime.model import RuntimeDataEnvelope
    from kairospy.core.market import MarketSelector, Quote
    from kairospy.core.reference import MarketResolver
    from kairospy.application.service.domains.market import MarketSubscription
    from kairospy.application.runtime.projection.account import AccountCurrentView
    from kairospy.application.strategy.events import StrategySignal


@dataclass(frozen=True, slots=True)
class StrategyContextServices:
    subscriptions: Any = None
    requests: Any = None
    emitted_events: list["RuntimeDataEnvelope"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StrategyContext:
    data: DataContext
    event: "StrategySignal | None" = None
    clock: "StrategySignal | None" = None
    state: Mapping[str, object] = field(default_factory=dict)
    intents: IntentJournal = field(default_factory=IntentJournal)
    controls: ControlJournal = field(default_factory=ControlJournal)
    views: ViewStore = field(default_factory=ViewStore)
    strategy_id: str = "strategy"
    phase: str = "idle"
    market: Any = None
    dataflow: Any = None
    market_resolver: "MarketResolver | None" = None
    services: StrategyContextServices | None = None
    _subscriptions: Any = None
    _requests: Any = None
    _emitted_intents: list[TradeIntent] = field(default_factory=list, repr=False, compare=False)
    _emitted_events: list["RuntimeDataEnvelope"] = field(default_factory=list, repr=False, compare=False)

    @property
    def now(self):
        if self.event is not None:
            return self.event.time
        if self.clock is not None:
            return self.clock.time
        return None

    @property
    def stream(self) -> str | None:
        if self.event is None:
            return None
        stream = getattr(self.event, "stream", None)
        return None if stream is None else str(stream)

    @property
    def market_event(self) -> "StrategySignal | None":
        return self.event if self.event is not None and self.event.domain == "market" else None

    @property
    def account_event(self) -> "StrategySignal | None":
        return self.event if self.event is not None and self.event.domain == "account" else None

    @property
    def order_event(self) -> "StrategySignal | None":
        return self.event if self.event is not None and self.event.domain == "execution" else None

    @property
    def system_event(self) -> "StrategySignal | None":
        return self.event if self.event is not None and self.event.domain == "system" else None

    @property
    def control(self) -> ControlFactory:
        return ControlFactory(strategy_id=self.strategy_id, requested_at=self.now, journal=self.controls)

    def view(self, key: str, default: object = None) -> object:
        return self.views.get(key, default)

    def data_records(self, *, domain: str | None = None, kind: str | None = None) -> tuple[object, ...]:
        if self.dataflow is None:
            raise RuntimeError("runtime has no data pipeline")
        return self.dataflow.records(domain=domain, kind=kind)

    def latest_data(self, *, domain: str | None = None, kind: str | None = None) -> object | None:
        if self.dataflow is None:
            raise RuntimeError("runtime has no data pipeline")
        return self.dataflow.latest(domain=domain, kind=kind)

    def account(self, key: str | None = None) -> "AccountCurrentView":
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

    def subscribe_market_data(
        self,
        instrument: object,
        *,
        selectors: Sequence["MarketSelector | type"],
        venue: str | None = None,
        market: str | None = None,
        identity: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> "MarketSubscription":
        subscriptions = self._subscription_service()
        if subscriptions is None:
            raise RuntimeError("runtime has no market subscription registry")
        resolved = self._market_resolver().resolve(instrument, venue=venue, market=market)
        subject_type, subject_id = _market_data_subject(selectors, resolved.instrument_id, resolved.market_id)
        return subscriptions.subscribe_data(
            subject_type,
            subject_id,
            selectors,
            venue=resolved.venue,
            market=resolved.market,
            source_symbol=resolved.source_symbol,
            requested_at=self.now,
            identity=identity,
            params={
                "market_id": resolved.market_id,
                "market_key": resolved.market_key,
                **dict(params or {}),
            },
        )

    def subscribe_subject_data(
        self,
        subject_type: str,
        subject_id: str,
        *,
        selectors: Sequence["MarketSelector | type"],
        venue: str | None = None,
        identity: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> "MarketSubscription":
        subscriptions = self._subscription_service()
        if subscriptions is None:
            raise RuntimeError("runtime has no market subscription registry")
        return subscriptions.subscribe_data(
            subject_type,
            subject_id,
            selectors,
            venue=venue,
            requested_at=self.now,
            identity=identity,
            params=params,
        )

    def unsubscribe(self, subscription: "MarketSubscription | str") -> None:
        subscriptions = self._subscription_service()
        if subscriptions is None:
            raise RuntimeError("runtime has no market subscription registry")
        subscriptions.unsubscribe(subscription)

    def request_quote(
        self,
        instrument: object,
        *,
        venue: str | None = None,
        market: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> "Quote | None":
        requests = self._request_service()
        if requests is None:
            raise RuntimeError("runtime has no market request service")
        return requests.request_quote(instrument, venue=venue, market=market, params=params)

    def target_position(
        self,
        instrument: object,
        quantity: Decimal | str | int | float,
        *,
        account: int | str | None = None,
        limit_price: Decimal | str | int | float | None = None,
        reason: str = "",
        intent_id: str | None = None,
    ) -> TradeIntent:
        if isinstance(account, bool):
            raise ValueError("account must be an integer index or account id")
        account_index = account if isinstance(account, int) else None
        account_id = str(account) if account is not None and account_index is None else None
        resolved = self._market_resolver().resolve(instrument)
        intent = target_position_intent(
            strategy_id=self.strategy_id,
            instrument_id=resolved.instrument_id,
            market_id=resolved.market_id,
            account_id=account_id,
            account_index=account_index,
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

    def _market_resolver(self) -> "MarketResolver":
        if self.market_resolver is not None:
            return self.market_resolver
        from kairospy.core.reference import MarketResolver

        return MarketResolver()

    def _subscription_service(self) -> Any:
        if self.services is not None and self.services.subscriptions is not None:
            return self.services.subscriptions
        return self._subscriptions

    def _request_service(self) -> Any:
        if self.services is not None and self.services.requests is not None:
            return self.services.requests
        return self._requests


Context = StrategyContext


def _market_data_subject(selectors: Sequence["MarketSelector | type"], instrument_id: str, market_id: str) -> tuple[str, str]:
    model_names = {
        selector.model.__name__ if hasattr(selector, "model") else selector.__name__ if isinstance(selector, type) else type(selector).__name__
        for selector in selectors
    }
    if model_names and model_names <= {"RateObservation", "FundingRate", "OpenInterest"}:
        return "market", market_id
    return "instrument", instrument_id


__all__ = ["Context", "StrategyContext", "StrategyContextServices"]
