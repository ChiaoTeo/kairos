from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from types import MappingProxyType
from typing import Mapping, Sequence

from kairospy.core.market import Bar, MarketEvent, MarketSelector, MarketSubject, OrderBookSnapshot, Quote, RateObservation, TradePrint, market_selector

from .planning import MarketStreamPlan, plan_market_streams


@dataclass(frozen=True, slots=True)
class MarketSubscriptionSpec:
    subject_type: str
    subject_id: str
    selectors: Sequence[MarketSelector | type]
    venue: str | None = None
    market: str | None = None
    source_symbol: str | None = None
    identity: str | None = None
    params: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.subject_type.strip() or not self.subject_id.strip():
            raise ValueError("market subscription subject is required")
        selectors = tuple(_coerce_selector(selector) for selector in self.selectors)
        if not selectors:
            raise ValueError("market subscription selectors are required")
        object.__setattr__(self, "selectors", selectors)
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True, slots=True)
class MarketSubscription:
    key: str
    spec: MarketSubscriptionSpec
    status: str = "active"
    requested_by: str = "strategy"
    requested_at: datetime | None = None
    provider: str = ""
    stream: str = ""
    stream_plans: tuple[MarketStreamPlan, ...] = ()
    last_event_time: datetime | None = None
    error: str = ""

    @property
    def kind(self) -> str:
        models = {_kind_for_model(selector.model) for selector in self.spec.selectors}
        return next(iter(models)) if len(models) == 1 else "market_data"

    @property
    def market_id(self) -> str:
        if self.spec.subject_type == "market":
            return self.spec.subject_id
        value = self.spec.params.get("market_id")
        return "" if value is None else str(value)

    @property
    def instrument_id(self) -> str:
        return self.spec.subject_id if self.spec.subject_type == "instrument" else ""

    @property
    def venue(self) -> str:
        return self.spec.venue or ""

    @property
    def market(self) -> str:
        return self.spec.market or ""

    @property
    def source_symbol(self) -> str:
        return self.spec.source_symbol or ""


class MarketSubscriptionRegistry:
    def __init__(self) -> None:
        self._subscriptions: dict[str, MarketSubscription] = {}

    def subscribe(self, spec: MarketSubscriptionSpec, *, requested_at: datetime | None = None) -> MarketSubscription:
        key = _subscription_key(spec)
        existing = self._subscriptions.get(key)
        if existing is not None:
            return existing
        stream_plans = plan_market_streams(spec)
        subscription = MarketSubscription(
            key=key,
            spec=spec,
            requested_at=requested_at,
            provider=spec.venue or "",
            stream=_subscription_stream(spec),
            stream_plans=stream_plans,
        )
        self._subscriptions[subscription.key] = subscription
        return subscription

    def subscribe_data(
        self,
        subject_type: str,
        subject_id: str,
        selectors: Sequence[MarketSelector | type],
        *,
        venue: str | None = None,
        market: str | None = None,
        source_symbol: str | None = None,
        requested_at: datetime | None = None,
        identity: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> MarketSubscription:
        return self.subscribe(
            MarketSubscriptionSpec(
                subject_type,
                subject_id,
                tuple(selectors),
                venue=venue,
                market=market,
                source_symbol=source_symbol,
                identity=identity,
                params=params or {},
            ),
            requested_at=requested_at,
        )

    def unsubscribe(self, value: MarketSubscription | str) -> None:
        key = value.key if isinstance(value, MarketSubscription) else str(value)
        self._subscriptions.pop(key, None)

    def list(self) -> tuple[MarketSubscription, ...]:
        return tuple(self._subscriptions[key] for key in sorted(self._subscriptions))

    def observe(self, event: MarketEvent) -> None:
        for key, subscription in tuple(self._subscriptions.items()):
            if subscription.spec.subject_type != event.subject.subject_type:
                continue
            if subscription.spec.subject_id != event.subject.subject_id:
                continue
            if not _subscription_observes_event(subscription.spec, event):
                continue
            self._subscriptions[key] = MarketSubscription(
                subscription.key,
                subscription.spec,
                status=subscription.status,
                requested_by=subscription.requested_by,
                requested_at=subscription.requested_at,
                provider=subscription.provider,
                stream=subscription.stream,
                stream_plans=subscription.stream_plans,
                last_event_time=event.observed_at,
                error=subscription.error,
            )


def _coerce_selector(value: MarketSelector | type) -> MarketSelector:
    if isinstance(value, MarketSelector):
        return value
    return market_selector(value)


def _subscription_key(spec: MarketSubscriptionSpec) -> str:
    suffix = _key_part(spec.source_symbol or spec.subject_id)
    selectors_key = sha1("|".join(selector.key for selector in spec.selectors).encode("utf-8")).hexdigest()[:12]
    identity = "" if spec.identity is None else f".{_key_part(spec.identity)}"
    return f"market.data.{suffix}.{selectors_key}{identity}"


def _subscription_stream(spec: MarketSubscriptionSpec) -> str:
    models = "_".join(sorted({selector.model.__name__.lower() for selector in spec.selectors}))
    parts = ("market", models or "data", spec.venue, spec.market, spec.source_symbol or spec.subject_id)
    return ".".join(_key_part(part) for part in parts if part)


def _subscription_observes_event(spec: MarketSubscriptionSpec, event: MarketEvent) -> bool:
    return any(isinstance(event.value, selector.model) and _basis_matches(selector, event.value) for selector in spec.selectors)


def _basis_matches(selector: MarketSelector, value: object) -> bool:
    if selector.basis is None:
        return True
    return getattr(value, "basis", None) == selector.basis


def _key_part(value: object) -> str:
    return "".join(character if character.isalnum() else "_" for character in str(value).lower()).strip("_")


def _kind_for_model(model: type) -> str:
    return {
        "Quote": "quote",
        "OrderBookSnapshot": "orderbook",
        "Bar": "bar",
        "TradePrint": "trade",
        "RateObservation": "rate",
    }.get(model.__name__, model.__name__.lower())


__all__ = ["MarketSubscription", "MarketSubscriptionRegistry", "MarketSubscriptionSpec"]
