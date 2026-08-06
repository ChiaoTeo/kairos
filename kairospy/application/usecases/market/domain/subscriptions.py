from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from types import MappingProxyType
from typing import Mapping, Sequence

from kairospy.domain.market import MarketSelector, market_selector
from kairospy.domain.market.selection import MarketSelectionQuery
from kairospy.domain.market import MarketSubscriptionSummary, MarketSubscriptionsView
from kairospy.domain.reference import MarketRef, ProviderId
from .specs import MarketOptions


@dataclass(frozen=True, slots=True)
class MarketDataSubscriptionSpec:
    market: MarketRef
    selectors: Sequence[MarketSelector | type]
    identity: str | None = None
    params: MarketOptions = MappingProxyType({})
    dataset_id: str | None = None
    provider: ProviderId | str | None = None

    def __post_init__(self) -> None:
        selectors = tuple(market_selector(selector) for selector in self.selectors)
        if not selectors:
            raise ValueError("data subscription selectors are required")
        if self.identity is not None and not self.identity.strip():
            raise ValueError("data subscription identity cannot be blank")
        if self.dataset_id is not None and not self.dataset_id.strip():
            raise ValueError("data subscription dataset_id cannot be blank")
        object.__setattr__(self, "selectors", selectors)
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
        object.__setattr__(self, "dataset_id", None if self.dataset_id is None else self.dataset_id.strip())
        object.__setattr__(self, "provider", None if self.provider is None else ProviderId(str(self.provider).strip()))

    @property
    def key(self) -> str:
        if self.dataset_id is not None:
            return f"data.{_key_part(self.dataset_id)}.{_key_part(self.identity or 'default')}"
        selectors_key = sha1("|".join(selector.key for selector in self.selectors).encode("utf-8")).hexdigest()[:12]
        identity = self.identity or "default"
        provider = _key_part(self.provider or self.market.exchange_id)
        return f"data.{provider}.{self.market.market_key}.{_key_part(identity)}.{selectors_key}"


@dataclass(frozen=True, slots=True)
class DataSubscription:
    key: str
    spec: MarketDataSubscriptionSpec


@dataclass(frozen=True, slots=True)
class DynamicMarketDataSubscriptionSpec:
    """A selection intent whose concrete contracts are reconciled by Market."""

    query: MarketSelectionQuery
    selectors: Sequence[MarketSelector | type]
    identity: str | None = None
    params: MarketOptions = MappingProxyType({})
    provider: ProviderId | str | None = None

    def __post_init__(self) -> None:
        selectors = tuple(market_selector(selector) for selector in self.selectors)
        if not selectors:
            raise ValueError("dynamic data subscription selectors are required")
        if self.identity is not None and not self.identity.strip():
            raise ValueError("dynamic data subscription identity cannot be blank")
        object.__setattr__(self, "selectors", selectors)
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
        object.__setattr__(self, "provider", None if self.provider is None else ProviderId(str(self.provider).strip()))

    @property
    def key(self) -> str:
        selectors_key = sha1("|".join(selector.key for selector in self.selectors).encode("utf-8")).hexdigest()[:12]
        query_key = sha1(repr(self.query).encode("utf-8")).hexdigest()[:12]
        identity = _key_part(self.identity or "default")
        provider = _key_part(self.provider or self.query.venue or "reference")
        return f"dynamic.{provider}.{identity}.{query_key}.{selectors_key}"


@dataclass(frozen=True, slots=True)
class MarketDataSubscriptionGroupSpec:
    """A batch of market subscription intents sharing one strategy identity."""

    specs: tuple[MarketDataSubscriptionSpec, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "specs", tuple(self.specs))
        if not self.specs:
            raise ValueError("subscription group cannot be empty")


@dataclass(frozen=True, slots=True)
class DataSubscriptionGroup:
    subscriptions: tuple[DataSubscription, ...]


class MarketSubscriptionService:
    def __init__(self) -> None:
        self._subscriptions: dict[str, DataSubscription] = {}
        self._dynamic_keys: set[str] = set()

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        subscription = DataSubscription(spec.key, spec)
        self._subscriptions[subscription.key] = subscription
        return subscription

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        key = subscription if isinstance(subscription, str) else subscription.key
        self._subscriptions.pop(key, None)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return tuple(self._subscriptions[key] for key in sorted(self._subscriptions))

    def register_dynamic(self, key: str) -> None:
        self._dynamic_keys.add(key)

    def unregister_dynamic(self, key: str) -> None:
        self._dynamic_keys.discard(key)

    def has_subscription_intents(self) -> bool:
        return bool(self._subscriptions or self._dynamic_keys)

    def subscriptions_view(self) -> MarketSubscriptionsView:
        summaries = tuple(subscription_summary(subscription) for subscription in self.subscriptions())
        return MarketSubscriptionsView(
            total_count=len(summaries),
            active_count=sum(1 for item in summaries if item.status == "active"),
            subscriptions=summaries,
        )


def subscription_summary(subscription: DataSubscription) -> MarketSubscriptionSummary:
    return MarketSubscriptionSummary(
        key=subscription.key,
        subject_type="market",
        subject_id=subscription.spec.market.market_key,
        kind="data",
        fields=tuple(selector.key for selector in subscription.spec.selectors),
        status="active",
        provider=subscription.spec.provider or subscription.spec.market.exchange_id,
        stream=subscription.spec.market.source_symbol,
    )


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "".join(character if character.isalnum() else "_" for character in text).strip("_") or "default"


__all__ = [
    "DataSubscription",
    "DynamicMarketDataSubscriptionSpec",
    "DataSubscriptionGroup",
    "MarketDataSubscriptionSpec",
    "MarketDataSubscriptionGroupSpec",
    "MarketSubscriptionService",
    "subscription_summary",
]
