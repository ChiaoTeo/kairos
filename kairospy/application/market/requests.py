from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SubscriptionRequest:
    """Market-owned strategy subscription request used at the process boundary."""

    subject: str
    selectors: tuple[str, ...] = ()
    exchange: str | None = None
    market_type: str | None = None
    asset_type: str | None = None
    identity: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)
    dynamic: bool = False

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("subscription subject is required")
        if any(not selector.strip() for selector in self.selectors):
            raise ValueError("subscription selectors must be non-empty strings")
        object.__setattr__(self, "selectors", tuple(self.selectors))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
