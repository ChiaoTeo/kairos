from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.support.runtime.domain.modes import RuntimeMode
from kairospy.application.support.runtime.services.orchestration.kernel import RuntimeKernel
from kairospy.application.support.runtime.services.orchestration.session import RuntimeSession
from kairospy.application.support.runtime.services.orchestration.state import RuntimeLaunchResult as StrategyRuntimeLaunchResult
from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.domain.lines import RuntimeEventLine
from kairospy.application.support.runtime.application.launch.pump import RuntimeEnvelopePump
from kairospy.domain.intent import IntentJournal
from kairospy.application.support.runtime.application.views import ViewStore


@dataclass(frozen=True, slots=True)
class RuntimeLaunchResult:
    launch_id: str
    mode: RuntimeMode
    runtime: StrategyRuntimeLaunchResult
    views: ViewStore
    intents: IntentJournal

    @property
    def decision_trace(self) -> tuple[object, ...]:
        view = self.views.get("strategy.decision_trace", None)
        return tuple(getattr(view, "records", ()) or ())

    @property
    def risk_snapshots(self) -> tuple[object, ...]:
        view = self.views.get("account.risk_snapshots", None)
        return tuple(getattr(view, "snapshots", ()) or ())


@dataclass(frozen=True, slots=True)
class RuntimeLaunchSession:
    launch_id: str
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

    async def run(self, source: RuntimeEventLine | None = None) -> RuntimeLaunchResult:
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
        return RuntimeLaunchResult(
            launch_id=self.launch_id,
            mode=self.mode,
            runtime=runtime,
            views=self.views,
            intents=self.intents,
        )


__all__ = ["RuntimeLaunchResult", "RuntimeLaunchSession"]
