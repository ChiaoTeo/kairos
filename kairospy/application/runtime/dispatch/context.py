from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Sequence, overload

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.ports import DataSubscription, MarketDataPort, MarketDataSubscriptionSpec
from kairospy.application.service.domain.market import parse_market_dataset_id
from kairospy.application.strategy import Context, ControlFactory, ControlJournal
from kairospy.core.intent import Intent, IntentJournal, TradeIntent, target_position_intent
from kairospy.core.market import MarketSelector
from kairospy.core.reference import MarketRef, MarketResolver
from kairospy.core.views import ViewStore


@dataclass(slots=True)
class RuntimeContext(Context):
    strategy_id: str
    event: RuntimeEnvelope | None = None
    state: Mapping[str, object] = field(default_factory=dict)
    intents: IntentJournal = field(default_factory=IntentJournal)
    data: MarketDataPort | None = None
    views: ViewStore = field(default_factory=ViewStore)
    controls: ControlJournal = field(default_factory=ControlJournal)
    emitted_intents: list[Intent] = field(default_factory=list)
    emitted_traces: list[Mapping[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", str(self.strategy_id).strip())
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        object.__setattr__(self, "state", MappingProxyType(dict(self.state)))

    def bind(self, event: RuntimeEnvelope | None) -> "RuntimeContext":
        self.event = event
        self.emitted_intents.clear()
        self.emitted_traces.clear()
        return self

    @property
    def control(self) -> ControlFactory:
        return ControlFactory(strategy_id=self.strategy_id, requested_at=self.now, journal=self.controls)

    @property
    def now(self) -> datetime | None:
        return None if self.event is None else self.event.time

    def intent(self, intent: Intent) -> None:
        if str(intent.strategy_id) != self.strategy_id:
            raise ValueError("intent strategy_id does not match runtime strategy")
        at = self.now or datetime.now(timezone.utc)
        self.intents.record_intent(intent, at=at)
        self.emitted_intents.append(intent)

    def trace(self, name: str, payload: Mapping[str, object]) -> None:
        label = str(name).strip()
        if not label:
            raise ValueError("trace name is required")
        self.emitted_traces.append(
            {
                "name": label,
                "time": self.now,
                "strategy_id": self.strategy_id,
                "payload": dict(payload),
            }
        )

    @overload
    def subscribe(
        self,
        instrument: MarketRef,
        *,
        selectors: Sequence[MarketSelector | type],
        identity: str | None = None,
    ) -> DataSubscription:
        ...

    @overload
    def subscribe(
        self,
        instrument: object | MarketRef,
        *,
        selectors: Sequence[MarketSelector | type],
        venue: str | None = None,
        market: str | None = None,
        identity: str | None = None,
    ) -> DataSubscription:
        ...

    def subscribe(
        self,
        instrument: object | MarketRef,
        *,
        selectors: Sequence[MarketSelector | type] | None = None,
        venue: str | None = None,
        market: str | None = None,
        identity: str | None = None,
    ) -> DataSubscription:
        if self.data is None:
            raise RuntimeError("runtime context has no data port")
        if selectors is None and isinstance(instrument, str) and instrument.startswith("market."):
            dataset = parse_market_dataset_id(instrument)
            return self.data.subscribe(
                MarketDataSubscriptionSpec(
                    dataset.market_ref,
                    (dataset.selector,),
                    identity=identity,
                    dataset_id=dataset.dataset_id,
                )
            )
        if selectors is None:
            raise ValueError("data subscription selectors are required unless subscribing by dataset id")
        market_ref = MarketResolver(default_venue=venue, default_market=market).resolve(instrument, venue=venue, market=market)
        return self.data.subscribe(MarketDataSubscriptionSpec(market_ref, selectors, identity=identity))

    def unsubscribe(self, subscription: object) -> None:
        if self.data is None:
            raise RuntimeError("runtime context has no data port")
        self.data.unsubscribe(subscription if isinstance(subscription, str) else getattr(subscription, "key", str(subscription)))

    def view(self, key: str, default: object = None) -> object:
        return self.views.get(key, default)

    def require_view(self, key: str) -> object:
        return self.views.require(key)

    def request_quote(self, instrument: object, *, venue: str | None = None, market: str | None = None) -> object | None:
        resolved = MarketResolver(default_venue=venue, default_market=market).resolve(instrument, venue=venue, market=market)
        quotes = self.views.get("market.quotes", None)
        for item in reversed(tuple(getattr(quotes, "quotes", ()))):
            if getattr(item, "instrument_id", None) == str(resolved.instrument_id) or getattr(item, "market_id", None) == str(resolved.market_id):
                return item
        return None

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
        resolved = MarketResolver().resolve(instrument)
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
        self.intent(intent)
        return intent

    def account(self, key: str | None = None) -> object:
        if key is not None:
            return self.views.require(key)
        account_keys = tuple(view_key for view_key in self.views.envelopes() if view_key.startswith("account.current."))
        if not account_keys:
            raise KeyError("no account view is available")
        if len(account_keys) > 1:
            raise ValueError("multiple account views are available; pass an account view key")
        return self.views.require(account_keys[0])


__all__ = ["RuntimeContext"]
