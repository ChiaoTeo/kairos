from __future__ import annotations

from dataclasses import dataclass

from kairospy.core.views import ViewStore

from ..kernel import RuntimeKernelSession, RuntimeKernel
from ..model import RuntimeDataEnvelope, RuntimeMode, StrategyRunResult
from ..source import EventSource


@dataclass(frozen=True, slots=True)
class RuntimeRunResult:
    runtime: StrategyRunResult
    views: ViewStore


@dataclass(frozen=True, slots=True)
class RuntimeRunSession:
    runtime: RuntimeKernel
    session: RuntimeKernelSession
    mode: RuntimeMode
    pre_events: tuple[RuntimeDataEnvelope, ...] = ()
    started_at: object = None

    @property
    def views(self) -> ViewStore:
        return self.runtime.views

    def run(self, source: EventSource) -> RuntimeRunResult:
        runtime = self.session.run(source)
        return RuntimeRunResult(runtime, self.runtime.views)


__all__ = ["RuntimeRunResult", "RuntimeRunSession"]
