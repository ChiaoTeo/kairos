from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kairospy.application.context import DataContext
from kairospy.application.context.control import ControlJournal
from kairospy.core.intent import IntentJournal
from kairospy.core.reference import MarketResolver
from kairospy.core.views import ViewStore
from kairospy.application.service.domains.market import MarketSubscriptionRegistry

from ..projection.market import MarketState
from .pipeline import RuntimeDataPipeline
from .requests import RuntimeRequestProviders


@dataclass(slots=True)
class RuntimeState:
    data: DataContext
    strategy_id: str
    market_resolver: MarketResolver
    strategy_state: dict[str, object]
    intents: IntentJournal
    controls: ControlJournal
    views: ViewStore
    data_pipeline: RuntimeDataPipeline
    subscriptions: MarketSubscriptionRegistry
    market: MarketState
    request_providers: RuntimeRequestProviders

    @classmethod
    def create(
        cls,
        *,
        data: DataContext,
        strategy_id: str,
        state: Mapping[str, object] | None = None,
        intents: IntentJournal | None = None,
        controls: ControlJournal | None = None,
        views: ViewStore | None = None,
        data_pipeline: RuntimeDataPipeline | None = None,
        market_state: MarketState | None = None,
        subscriptions: MarketSubscriptionRegistry | None = None,
        market_resolver: MarketResolver | None = None,
        request_providers: RuntimeRequestProviders | None = None,
    ) -> "RuntimeState":
        resolver = market_resolver or getattr(data, "markets", None) or MarketResolver()
        registry = subscriptions or MarketSubscriptionRegistry()
        market = market_state or MarketState(registry)
        if market.subscriptions is not registry:
            registry = market.subscriptions
        return cls(
            data=data,
            strategy_id=strategy_id,
            market_resolver=resolver,
            strategy_state=dict(state or {}),
            intents=intents or IntentJournal(),
            controls=controls or ControlJournal(),
            views=views or ViewStore(),
            data_pipeline=data_pipeline or RuntimeDataPipeline(),
            subscriptions=registry,
            market=market,
            request_providers=request_providers or RuntimeRequestProviders(),
        )


__all__ = ["RuntimeState"]
