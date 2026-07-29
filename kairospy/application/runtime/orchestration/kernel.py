from __future__ import annotations

from typing import Mapping

from kairospy.application.runtime.dispatch.context import RuntimeContext
from kairospy.application.runtime.dispatch.dispatcher import RuntimeDispatcher
from kairospy.application.runtime.orchestration.pipeline import RuntimeServicePipeline
from kairospy.application.runtime.orchestration.session import RuntimeSession
from kairospy.application.runtime.orchestration.state import RuntimeFrame, RuntimeRunResult, Callback
from kairospy.application.runtime.protocol import RuntimeEnvelope, RuntimeEventLine
from kairospy.application.runtime.services import (
    AccountService,
    AccountServiceProjectionProvider,
    MarketDataProjectionProvider,
    MarketDataService,
    ReferenceService,
    ReferenceServiceProjectionProvider,
)
from kairospy.application.runtime.services.component import RuntimeComponentProvider, RuntimeViewPublisher
from kairospy.application.strategy import ControlJournal, Strategy
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.intent import IntentJournal
from kairospy.core.views import ViewStore


class RuntimeKernel:
    def __init__(
        self,
        strategy: Strategy,
        *,
        state: Mapping[str, object] | None = None,
        intents: IntentJournal | None = None,
        controls: ControlJournal | None = None,
        views: ViewStore | None = None,
        data: MarketDataService | None = None,
        account: AccountService | None = None,
        reference: ReferenceService | None = None,
        execution: ExecutionCoordinator | None = None,
        providers: tuple[RuntimeComponentProvider, ...] = (),
        components: tuple[RuntimeViewPublisher, ...] = (),
    ) -> None:
        if not strategy.strategy_id.strip():
            raise ValueError("strategy_id is required")
        self.strategy = strategy
        self.state = dict(state or {})
        self.intents = intents or IntentJournal()
        self.controls = controls or ControlJournal()
        self.views = views or ViewStore()
        self.data = data
        self.account = account
        self.reference = reference
        self.execution_coordinator = execution
        self.providers = (
            *((MarketDataProjectionProvider(self.data),) if self.data is not None else ()),
            *((AccountServiceProjectionProvider(self.account),) if self.account is not None else ()),
            *((ReferenceServiceProjectionProvider(self.reference),) if self.reference is not None else ()),
            *providers,
        )
        self.services = RuntimeServicePipeline(
            views=self.views,
            strategy_id=self.strategy.strategy_id,
            intents=self.intents,
            data=self.data,
            account=self.account,
            reference=self.reference,
            providers=self.providers,
            components=components,
        )
        self.context = RuntimeContext(
            strategy_id=self.strategy.strategy_id,
            state=self.state,
            intents=self.intents,
            controls=self.controls,
            data=self.data,
            views=self.views,
        )
        self.dispatcher = RuntimeDispatcher(
            strategy=self.strategy,
            intents=self.intents,
            context=self.context,
        )

    def start(self) -> RuntimeSession:
        frame = RuntimeFrame()
        self.dispatcher.start(frame)
        self.services.publish()
        return RuntimeSession(self.dispatcher, self.services, frame)

    async def run(self, source: RuntimeEventLine | None = None) -> RuntimeRunResult:
        line = source or self.data or self.account
        if line is None:
            raise ValueError("runtime event line is required")
        return await self.start().run(line)

__all__ = [
    "RuntimeKernel",
    "RuntimeFrame",
    "RuntimeRunResult",
    "RuntimeSession",
    "Callback",
]
