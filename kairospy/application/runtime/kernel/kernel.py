from __future__ import annotations

from typing import Mapping

from kairospy.application.context import DataContext, StrategyContext
from kairospy.application.context.control import ControlJournal
from kairospy.core.intent import IntentJournal
from kairospy.core.reference import MarketResolver
from kairospy.core.views import ViewStore
from kairospy.application.service.domains.market import MarketSubscriptionRegistry
from kairospy.application.strategy.protocol import Strategy

from ..model import RuntimeDataEnvelope, StrategyRunResult
from ..model import result as runtime_result
from ..projection import IntentJournalProjection, RiskEventProjection, RuntimeComponent
from ..projection.market import MarketProjection, MarketState
from ..projection.registry import RuntimeProjectionRegistry, SystemProjectionAdapter
from ..projection.system import RuntimeSystemProjection
from ..source import EventSource
from .context import RuntimeContextFactory
from .engine import RuntimeEngine, RuntimeRunFrame
from .output import IntentHandler, RuntimeOutputProcessor, SubscriptionHandler
from .pipeline import RuntimeDataPipeline
from .queue import RuntimeQueue
from .requests import RuntimeRequestProviders
from .services import RuntimeServices
from .state import RuntimeState


class RuntimeKernelSession:
    def __init__(
        self,
        runtime: "RuntimeKernel",
        frame: RuntimeRunFrame,
        *,
        intent_handler: IntentHandler | None = None,
        subscription_handler: SubscriptionHandler | None = None,
    ) -> None:
        self.runtime = runtime
        self.frame = frame
        self.intent_handler = intent_handler
        self.subscription_handler = subscription_handler

    @property
    def state(self) -> RuntimeRunFrame:
        return self.frame

    def process(self, record: RuntimeDataEnvelope) -> None:
        self._ensure_active()
        pending = RuntimeQueue.pending((record,))
        while True:
            event = pending.next()
            if event is None:
                break
            result = self.runtime.engine.process(
                self.frame,
                event,
                intent_handler=self.intent_handler,
                subscription_handler=self.subscription_handler,
            )
            pending.extend(result.follow_up_events)

    def finish(self) -> StrategyRunResult:
        self._ensure_active()
        return self.runtime.engine.finish(
            self.frame,
            intent_handler=self.intent_handler,
            subscription_handler=self.subscription_handler,
        )

    def run(self, source: EventSource) -> StrategyRunResult:
        for record in source.events():
            self.process(record)
        return self.finish()

    def _ensure_active(self) -> None:
        if self.frame.finished:
            raise RuntimeError("strategy run session is already finished")


class RuntimeKernel:
    def __init__(
        self,
        strategy: Strategy,
        data: DataContext,
        *,
        state: Mapping[str, object] | None = None,
        intents: IntentJournal | None = None,
        controls: ControlJournal | None = None,
        views: ViewStore | None = None,
        components: tuple[RuntimeComponent, ...] = (),
        data_pipeline: RuntimeDataPipeline | None = None,
        market_state: MarketState | None = None,
        subscriptions: MarketSubscriptionRegistry | None = None,
        market_resolver: MarketResolver | None = None,
        request_providers: RuntimeRequestProviders | None = None,
    ) -> None:
        if not strategy.strategy_id.strip():
            raise ValueError("strategy_id is required")
        self.strategy = strategy
        self.runtime_state = RuntimeState.create(
            data=data,
            strategy_id=strategy.strategy_id,
            state=state,
            intents=intents,
            controls=controls,
            views=views,
            data_pipeline=data_pipeline,
            market_state=market_state,
            subscriptions=subscriptions,
            market_resolver=market_resolver,
            request_providers=request_providers,
        )
        self.data = self.runtime_state.data
        self.market_resolver = self.runtime_state.market_resolver
        self.state = self.runtime_state.strategy_state
        self.intents = self.runtime_state.intents
        self.controls = self.runtime_state.controls
        self.views = self.runtime_state.views
        self.data_pipeline = self.runtime_state.data_pipeline
        self.subscriptions = self.runtime_state.subscriptions
        self.market_state = self.runtime_state.market
        self.request_providers = self.runtime_state.request_providers
        self.components = (RiskEventProjection(), *tuple(components))
        self.system_projection = RuntimeSystemProjection(
            strategy_id=strategy.strategy_id,
            data=self.data,
            data_pipeline=self.data_pipeline,
            controls=self.controls,
        )
        self.projections = RuntimeProjectionRegistry(
            (
                SystemProjectionAdapter(self.system_projection),
                IntentJournalProjection(strategy.strategy_id, self.intents),
                MarketProjection(self.market_state),
            ),
            components=self.components,
        )
        self.views.register(self.data_pipeline.schema)
        self.projections.register(self.views)
        self.output_processor = RuntimeOutputProcessor(
            strategy_id=strategy.strategy_id,
            intents=self.intents,
            subscriptions=self.subscriptions,
        )
        self.context_factory = RuntimeContextFactory.from_state(self.runtime_state)
        self.services = RuntimeServices(
            context_factory=self.context_factory,
            output=self.output_processor,
            request_providers=self.request_providers,
        )
        self.engine = RuntimeEngine(
            strategy=self.strategy,
            state=self.runtime_state,
            services=self.services,
            projections=self.projections,
        )
        self.runtime_result = runtime_result

    def run(
        self,
        source: EventSource,
        *,
        intent_handler: IntentHandler | None = None,
        subscription_handler: SubscriptionHandler | None = None,
    ) -> StrategyRunResult:
        return self.start(intent_handler=intent_handler, subscription_handler=subscription_handler).run(source)

    def start(
        self,
        *,
        intent_handler: IntentHandler | None = None,
        subscription_handler: SubscriptionHandler | None = None,
    ) -> RuntimeKernelSession:
        return RuntimeKernelSession(
            self,
            self.engine.start(intent_handler=intent_handler, subscription_handler=subscription_handler),
            intent_handler=intent_handler,
            subscription_handler=subscription_handler,
        )

    def _context(
        self,
        *,
        event: RuntimeDataEnvelope | None = None,
        clock: RuntimeDataEnvelope | None = None,
        phase: str,
    ) -> StrategyContext:
        return self.context_factory.create(event=event, clock=clock, phase=phase)


__all__ = [
    "IntentHandler",
    "RuntimeKernel",
    "RuntimeKernelSession",
    "SubscriptionHandler",
]
