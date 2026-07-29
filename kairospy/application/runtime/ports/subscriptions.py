from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from types import MappingProxyType
from typing import Mapping, Sequence

from kairospy.core.market import MarketSelector, market_selector
from kairospy.core.reference import MarketRef


@dataclass(frozen=True, slots=True)
class MarketDataSubscriptionSpec:
    market: MarketRef
    selectors: Sequence[MarketSelector | type]
    identity: str | None = None
    params: Mapping[str, object] = MappingProxyType({})
    dataset_id: str | None = None

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

    @property
    def key(self) -> str:
        if self.dataset_id is not None:
            return f"data.{_key_part(self.dataset_id)}.{_key_part(self.identity or 'default')}"
        selectors_key = sha1("|".join(selector.key for selector in self.selectors).encode("utf-8")).hexdigest()[:12]
        identity = self.identity or "default"
        return f"data.{self.market.market_key}.{_key_part(identity)}.{selectors_key}"


@dataclass(frozen=True, slots=True)
class DataSubscription:
    key: str
    spec: MarketDataSubscriptionSpec


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "".join(character if character.isalnum() else "_" for character in text).strip("_") or "default"


__all__ = ["DataSubscription", "MarketDataSubscriptionSpec"]
