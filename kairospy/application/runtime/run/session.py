from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.runtime.model import RuntimeMode
from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.orchestration.session import RuntimeSession
from kairospy.application.runtime.orchestration.state import RuntimeRunResult as StrategyRuntimeRunResult
from kairospy.application.runtime.protocol import RuntimeEnvelope, RuntimeEventLine
from kairospy.application.runtime.run.pump import RuntimeEnvelopePump
from kairospy.application.strategy import ControlJournal
from kairospy.core.intent import IntentJournal
from kairospy.core.views import ViewStore


@dataclass(frozen=True, slots=True)
class RuntimeRunResult:
    run_id: str
    mode: RuntimeMode
    runtime: StrategyRuntimeRunResult
    views: ViewStore
    intents: IntentJournal
    controls: ControlJournal


@dataclass(frozen=True, slots=True)
class RuntimeRunSession:
    run_id: str
    mode: RuntimeMode
    kernel: RuntimeKernel
    session: RuntimeSession
    pre_events: tuple[RuntimeEnvelope, ...] = ()
    started_at: datetime | None = None

    @property
    def views(self) -> ViewStore:
        return self.kernel.views

    @property
    def intents(self) -> IntentJournal:
        return self.kernel.intents

    @property
    def controls(self) -> ControlJournal:
        return self.kernel.controls

    async def run(self, source: RuntimeEventLine | None = None) -> RuntimeRunResult:
        line = source or self.kernel.data or self.kernel.account
        if line is None:
            raise ValueError("runtime event line is required")
        runtime = await self.session.run(
            RuntimeEnvelopePump(
                line,
                self.mode,
                pre_events=self.pre_events,
                started_at=self.started_at,
            )
        )
        return RuntimeRunResult(
            run_id=self.run_id,
            mode=self.mode,
            runtime=runtime,
            views=self.views,
            intents=self.intents,
            controls=self.controls,
        )


__all__ = ["RuntimeRunResult", "RuntimeRunSession"]
