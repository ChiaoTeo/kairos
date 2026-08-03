from __future__ import annotations

from kairospy.application.support.runtime.application.dispatch.context import RuntimeContext
from kairospy.application.support.runtime.application.dispatch.dispatcher import RuntimeDispatcher
from kairospy.application.support.runtime.application.projection import RuntimeProjector
from kairospy.application.support.runtime.services.orchestration.pipeline import RuntimeProjectionPipeline
from kairospy.application.support.runtime.services.orchestration.session import RuntimeSession
from kairospy.application.support.runtime.services.orchestration.state import (
    RuntimeFrame,
    RuntimeLaunchResult,
    RuntimeStores,
    RuntimeStep,
    Callback,
)
from kairospy.application.support.runtime.domain.components import RuntimeComponents
from kairospy.application.support.runtime.domain.lines import MergedRuntimeEventLine, RuntimeEventLine
from kairospy.application.usecases.strategy.protocol import Strategy


class RuntimeKernel:
    def __init__(
        self,
        strategy: Strategy,
        *,
        components: RuntimeComponents | None = None,
        stores: RuntimeStores | None = None,
        services: object | None = None,
        processors: RuntimeProjector | None = None,
    ) -> None:
        if not strategy.strategy_id.strip():
            raise ValueError("strategy_id is required")
        self.strategy = strategy
        self.components = components or RuntimeComponents()
        self.stores = stores or RuntimeStores()
        self.state = dict(self.stores.strategy_state)
        self.intents = self.stores.intents
        self.views = self.stores.views
        self.data = self.components.market
        self.account = self.components.account
        self.reference = self.components.reference
        self.trading_execution = self.components.execution
        if processors is None and services is not None:
            factory = getattr(services, "projectors", None)
            if callable(factory):
                processors = factory(strategy_id=self.strategy.strategy_id, intents=self.intents)
        if processors is None:
            raise ValueError("runtime processors must be supplied by composition")
        self.processors = processors
        self.pipeline = RuntimeProjectionPipeline(
            views=self.views,
            processors=self.processors,
        )
        self.context = RuntimeContext(
            strategy_id=self.strategy.strategy_id,
            state=self.state,
            intents=self.intents,
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
        self.pipeline.publish()
        return RuntimeSession(self.dispatcher, self.pipeline, frame)

    async def run(self, source: RuntimeEventLine | None = None) -> RuntimeLaunchResult:
        line = self._event_line(source)
        if line is None:
            raise ValueError("runtime event line is required")
        return await self.start().run(line)

    def _event_line(self, source: RuntimeEventLine | None = None) -> RuntimeEventLine | None:
        lines: list[RuntimeEventLine] = []
        for candidate in (source, self.data, self.account, self.reference, self.trading_execution):
            if candidate is None or any(candidate is item for item in lines):
                continue
            if callable(getattr(candidate, "events", None)):
                lines.append(candidate)  # type: ignore[arg-type]
        if not lines:
            return None
        if len(lines) == 1:
            return lines[0]
        return MergedRuntimeEventLine(lines)

__all__ = [
    "RuntimeKernel",
    "RuntimeFrame",
    "RuntimeComponents",
    "RuntimeLaunchResult",
    "RuntimeStores",
    "RuntimeStep",
    "RuntimeSession",
    "Callback",
]
