from __future__ import annotations

from typing import Mapping

from kairospy.application.runtime.dispatch.context import RuntimeContext
from kairospy.application.runtime.dispatch.dispatcher import RuntimeDispatcher
from kairospy.application.runtime.orchestration.pipeline import RuntimePortPipeline
from kairospy.application.runtime.orchestration.session import RuntimeSession
from kairospy.application.runtime.orchestration.state import RuntimeFrame, RuntimeRunResult, Callback
from kairospy.application.runtime.protocol import MergedRuntimeEventLine, RuntimeEventLine
from kairospy.application.runtime.ports import AccountJournalSink, AccountPort, MarketDataPort, ReferencePort, TradingExecutionPort
from kairospy.application.runtime.processors.system import RuntimeProcessors, runtime_processors
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
        data: MarketDataPort | None = None,
        account: AccountPort | None = None,
        reference: ReferencePort | None = None,
        trading_execution: TradingExecutionPort | None = None,
        execution_coordinator: ExecutionCoordinator | None = None,
        account_journal: AccountJournalSink | None = None,
        timeline_journal: object | None = None,
        timeline_sample_interval: object = "1m",
        processors: RuntimeProcessors | None = None,
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
        self.trading_execution = trading_execution
        self.execution_coordinator = execution_coordinator
        self.account_journal = account_journal
        self.timeline_journal = timeline_journal
        self.timeline_sample_interval = timeline_sample_interval
        self.processors = processors or self._processors()
        self.pipeline = RuntimePortPipeline(
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

    async def run(self, source: RuntimeEventLine | None = None) -> RuntimeRunResult:
        line = self._event_line(source)
        if line is None:
            raise ValueError("runtime event line is required")
        return await self.start().run(line)

    def _processors(self) -> RuntimeProcessors:
        return runtime_processors(
            strategy_id=self.strategy.strategy_id,
            intents=self.intents,
            data=self.data,
            account=self.account,
            reference=self.reference,
            trading_execution=self.trading_execution,
            execution_coordinator=self.execution_coordinator,
            account_journal=self.account_journal,
            timeline_journal=self.timeline_journal,
            timeline_sample_interval=self.timeline_sample_interval,
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
    "RuntimeRunResult",
    "RuntimeSession",
    "Callback",
]
