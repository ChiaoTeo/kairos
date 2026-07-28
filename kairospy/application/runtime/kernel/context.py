from __future__ import annotations

from typing import Mapping

from kairospy.application.context import DataContext, StrategyContext, StrategyContextServices
from kairospy.application.context.control import ControlJournal
from kairospy.core.intent import IntentJournal
from kairospy.core.reference import MarketResolver
from kairospy.core.views import ViewStore
from kairospy.application.service.domains.market import MarketSubscriptionRegistry
from kairospy.application.strategy.events import StrategySignal

from ..model import RuntimeDataEnvelope
from ..projection.market import MarketAccess, MarketState
from .pipeline import RuntimeDataPipeline
from .requests import MarketRequestService, RuntimeRequestProviders
from .state import RuntimeState


class RuntimeContextFactory:
    def __init__(
        self,
        *,
        data: DataContext,
        strategy_id: str,
        state: Mapping[str, object],
        intents: IntentJournal,
        controls: ControlJournal,
        views: ViewStore,
        market_resolver: MarketResolver,
        market_state: MarketState,
        subscriptions: MarketSubscriptionRegistry,
        data_pipeline: RuntimeDataPipeline,
        request_providers: RuntimeRequestProviders | None = None,
    ) -> None:
        self.data = data
        self.strategy_id = strategy_id
        self.state = state
        self.intents = intents
        self.controls = controls
        self.views = views
        self.market_resolver = market_resolver
        self.market_state = market_state
        self.subscriptions = subscriptions
        self.data_pipeline = data_pipeline
        self.request_providers = request_providers or RuntimeRequestProviders()

    @classmethod
    def from_state(cls, state: RuntimeState) -> "RuntimeContextFactory":
        return cls(
            data=state.data,
            strategy_id=state.strategy_id,
            state=state.strategy_state,
            intents=state.intents,
            controls=state.controls,
            views=state.views,
            market_resolver=state.market_resolver,
            market_state=state.market,
            subscriptions=state.subscriptions,
            data_pipeline=state.data_pipeline,
            request_providers=state.request_providers,
        )

    def create(
        self,
        *,
        event: RuntimeDataEnvelope | None = None,
        clock: RuntimeDataEnvelope | None = None,
        phase: str,
    ) -> StrategyContext:
        strategy_event = None if event is None else strategy_signal(event)
        strategy_clock = None if clock is None else strategy_signal(clock)
        emitted_events: list[RuntimeDataEnvelope] = []
        request_service = MarketRequestService(
            self.market_resolver,
            phase=phase,
            providers=self.request_providers,
            emit_event=emitted_events.append,
        )
        services = StrategyContextServices(
            subscriptions=self.subscriptions,
            requests=request_service,
            emitted_events=emitted_events,
        )
        return StrategyContext(
            self.data,
            event=strategy_event,
            clock=strategy_clock,
            state=self.state,
            intents=self.intents,
            controls=self.controls,
            views=self.views,
            strategy_id=self.strategy_id,
            phase=phase,
            market=MarketAccess(self.market_resolver, self.market_state),
            market_resolver=self.market_resolver,
            services=services,
            _subscriptions=self.subscriptions,
            _requests=request_service,
            dataflow=self.data_pipeline,
            _emitted_events=emitted_events,
        )


def strategy_signal(event: RuntimeDataEnvelope) -> StrategySignal:
    return StrategySignal(
        domain=str(event.domain),
        kind=event.kind,
        time=event.time,
        sequence=event.sequence,
        stream=event.stream,
        source=event.source,
        metadata=event.metadata,
    )


__all__ = ["RuntimeContextFactory", "strategy_signal"]
