from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from kairospy.application.context import DataContext
from kairospy.core.reference import MarketResolver
from kairospy.application.strategy import Strategy

from ..kernel import IntentHandler, RuntimeRequestProviders, SubscriptionHandler
from ..model import RuntimeDataEnvelope, RunProfile, RuntimeMode
from ..projection.base import RuntimeComponent
from ..source import AsyncEventSource, EventSource


@dataclass(frozen=True, slots=True)
class RuntimeStateConfig:
    data: DataContext
    market_resolver: MarketResolver | None = None


@dataclass(frozen=True, slots=True)
class RuntimeServiceConfig:
    intent_handler: IntentHandler | None = None
    subscription_handler: SubscriptionHandler | None = None
    request_providers: RuntimeRequestProviders | None = None


@dataclass(frozen=True, slots=True)
class RuntimeProjectionConfig:
    components: tuple[RuntimeComponent, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeRunSpec:
    run_id: str
    profile: RunProfile
    strategy: Strategy
    source: EventSource | AsyncEventSource
    state_config: RuntimeStateConfig
    service_config: RuntimeServiceConfig = field(default_factory=RuntimeServiceConfig)
    projection_config: RuntimeProjectionConfig = field(default_factory=RuntimeProjectionConfig)
    pre_events: tuple[RuntimeDataEnvelope, ...] = ()
    started_at: datetime | None = None

    @property
    def mode(self) -> RuntimeMode:
        return self.profile.mode


__all__ = [
    "RuntimeProjectionConfig",
    "RuntimeRunSpec",
    "RuntimeServiceConfig",
    "RuntimeStateConfig",
]
