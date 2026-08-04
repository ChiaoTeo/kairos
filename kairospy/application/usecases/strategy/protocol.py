from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Protocol

from kairospy.domain.intent import Intent

from .domain.context import StrategyContextProtocol
from .domain.context import StrategyReferenceCapability
from .domain.events import Signal


class Strategy(Protocol):
    strategy_id: str

    def on_start(self, context: StrategyContextProtocol) -> None:
        ...

    def on_data(self, context: StrategyContextProtocol, signal: Signal) -> None:
        ...

    def on_intent(self, context: StrategyContextProtocol, intent: Intent) -> None:
        ...

    def on_clock(self, context: StrategyContextProtocol, signal: Signal) -> None:
        ...

    def on_system(self, context: StrategyContextProtocol, signal: Signal) -> None:
        ...

    def on_end(self, context: StrategyContextProtocol) -> None:
        ...


class StrategyBase:
    strategy_id = "strategy"

    def on_start(self, context: StrategyContextProtocol) -> None:
        return None

    def on_data(self, context: StrategyContextProtocol, signal: Signal) -> None:
        return None

    def on_intent(self, context: StrategyContextProtocol, intent: Intent) -> None:
        return None

    def on_clock(self, context: StrategyContextProtocol, signal: Signal) -> None:
        return None

    def on_system(self, context: StrategyContextProtocol, signal: Signal) -> None:
        return None

    def on_end(self, context: StrategyContextProtocol) -> None:
        return None


@dataclass(frozen=True, slots=True)
class StrategySubscriptionRequest:
    """Strategy-owned subscription intent translated by system/biz."""

    subject: object
    selectors: tuple[object, ...] = ()
    exchange: str | None = None
    market_type: str | None = None
    identity: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "selectors", tuple(self.selectors))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True, slots=True)
class StrategySubscriptionGroupRequest:
    requests: tuple[StrategySubscriptionRequest, ...]

    def __post_init__(self) -> None:
        requests = tuple(self.requests)
        if not requests:
            raise ValueError("strategy subscription group cannot be empty")
        object.__setattr__(self, "requests", requests)


__all__ = [
    "Strategy",
    "StrategyBase",
    "StrategyReferenceCapability",
    "StrategySubscriptionGroupRequest",
    "StrategySubscriptionRequest",
]
