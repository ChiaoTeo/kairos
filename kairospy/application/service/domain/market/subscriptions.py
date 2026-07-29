from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from types import MappingProxyType
from typing import Mapping, Sequence

from kairospy.core.market import MarketSelector, market_selector
from kairospy.core.reference import MarketRef


@dataclass(frozen=True, slots=True)
class MarketSubscriptionSpec:
    subject_type: str
    subject_id: str
    selectors: tuple[MarketSelector, ...]
    venue: str | None = None
    market: str | None = None
    source_symbol: str | None = None
    identity: str | None = None
    params: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.subject_type.strip() or not self.subject_id.strip():
            raise ValueError("market subscription subject is required")
        selectors = tuple(market_selector(selector) for selector in self.selectors)
        if not selectors:
            raise ValueError("market subscription selectors are required")
        object.__setattr__(self, "selectors", selectors)
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    @property
    def market_ref(self) -> MarketRef:
        venue = self.venue or "unknown"
        market = self.market or "unknown"
        symbol = self.source_symbol or self.subject_id
        ref = MarketRef.ephemeral(venue=venue, market=market, source_symbol=symbol)
        if self.subject_type == "market":
            return MarketRef(self.subject_id, ref.instrument_id, ref.market_key, venue, market, symbol)
        if self.subject_type == "instrument":
            return MarketRef(ref.market_id, self.subject_id, ref.market_key, venue, market, symbol)
        return ref


@dataclass(frozen=True, slots=True)
class MarketSubscription:
    key: str
    spec: MarketSubscriptionSpec


class MarketSubscriptionRegistry:
    def __init__(self) -> None:
        self._subscriptions: dict[str, MarketSubscription] = {}

    def subscribe_data(
        self,
        subject_type: str,
        subject_id: str,
        selectors: Sequence[MarketSelector | type],
        *,
        venue: str | None = None,
        market: str | None = None,
        source_symbol: str | None = None,
        requested_at: object | None = None,
        identity: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> MarketSubscription:
        spec = MarketSubscriptionSpec(
            subject_type,
            subject_id,
            tuple(market_selector(selector) for selector in selectors),
            venue=venue,
            market=market,
            source_symbol=source_symbol,
            identity=identity,
            params={"requested_at": requested_at, **dict(params or {})} if requested_at is not None else params or {},
        )
        key = _subscription_key(spec)
        subscription = MarketSubscription(key, spec)
        self._subscriptions[key] = subscription
        return subscription

    def unsubscribe(self, subscription: MarketSubscription | str) -> None:
        key = subscription if isinstance(subscription, str) else subscription.key
        self._subscriptions.pop(key, None)

    def subscriptions(self) -> tuple[MarketSubscription, ...]:
        return tuple(self._subscriptions[key] for key in sorted(self._subscriptions))


def _subscription_key(spec: MarketSubscriptionSpec) -> str:
    selectors_key = sha1("|".join(selector.key for selector in spec.selectors).encode("utf-8")).hexdigest()[:12]
    identity = spec.identity or "default"
    return f"data.{spec.market_ref.market_key}.{_key_part(identity)}.{selectors_key}"


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "".join(character if character.isalnum() else "_" for character in text).strip("_") or "default"


__all__ = [
    "MarketSubscription",
    "MarketSubscriptionRegistry",
    "MarketSubscriptionSpec",
]
