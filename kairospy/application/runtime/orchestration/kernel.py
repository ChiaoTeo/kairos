from __future__ import annotations

from kairospy.application.runtime.dispatch.context import RuntimeContext
from kairospy.application.runtime.dispatch.dispatcher import RuntimeDispatcher
from kairospy.application.runtime.orchestration.pipeline import RuntimeProjectionPipeline
from kairospy.application.runtime.orchestration.session import RuntimeSession
from kairospy.application.runtime.orchestration.state import (
    RuntimeFrame,
    RuntimePorts,
    RuntimeLaunchResult,
    RuntimeStores,
    RuntimeStep,
    Callback,
)
from kairospy.application.service.runtime.services import RuntimeApplicationServices
from kairospy.application.protocol import MergedRuntimeEventLine, RuntimeEventLine
from kairospy.application.runtime.processors.system import RuntimeProcessors, runtime_processors
from kairospy.application.strategy import Strategy


class RuntimeKernel:
    def __init__(
        self,
        strategy: Strategy,
        *,
        ports: RuntimePorts | None = None,
        stores: RuntimeStores | None = None,
        services: RuntimeApplicationServices | None = None,
        processors: RuntimeProcessors | None = None,
    ) -> None:
        if not strategy.strategy_id.strip():
            raise ValueError("strategy_id is required")
        self.strategy = strategy
        self.ports = ports or RuntimePorts()
        self.stores = stores or RuntimeStores()
        self.services = services or RuntimeApplicationServices()
        self.state = dict(self.stores.strategy_state)
        self.intents = self.stores.intents
        self.controls = self.stores.controls
        self.views = self.stores.views
        self.data = self.ports.data
        self.account = self.ports.account
        self.reference = self.ports.reference
        self.trading_execution = self.ports.trading_execution
        self.processors = processors or self._processors()
        self.pipeline = RuntimeProjectionPipeline(
            views=self.views,
            processors=self.processors,
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
        self.pipeline.publish()
        return RuntimeSession(self.dispatcher, self.pipeline, frame)

    async def run(self, source: RuntimeEventLine | None = None) -> RuntimeLaunchResult:
        line = self._event_line(source)
        if line is None:
            raise ValueError("runtime event line is required")
        return await self.start().run(line)

    def _processors(self) -> RuntimeProcessors:
        return runtime_processors(
            strategy_id=self.strategy.strategy_id,
            intents=self.intents,
            services=self.services,
        )

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
    "RuntimePorts",
    "RuntimeLaunchResult",
    "RuntimeStores",
    "RuntimeStep",
    "RuntimeSession",
    "Callback",
]
