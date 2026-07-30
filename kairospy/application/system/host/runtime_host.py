from __future__ import annotations

import asyncio
from typing import Mapping

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.ports import AccountPort, TradingExecutionPort
from kairospy.application.runtime.run import RuntimeRunResult, RuntimeRunSession
from kairospy.application.runtime.run.pump import RuntimeEnvelopePump
from kairospy.application.runtime.protocol import RuntimeEventLine, close_event_line
from kairospy.application.system.artifacts.output import RunOutput
from kairospy.application.system.projectors import RunArtifactProjector
from kairospy.core.execution import ExecutionCoordinator

from .lifecycle import NoopTradingLifecycle
from .resources import TradingRuntimeResources, TradingRunSpec


class TradingSystem:
    def __init__(self, spec: TradingRunSpec) -> None:
        self.spec = spec

    def run(self) -> RuntimeRunResult:
        resources = self.spec.resources
        lifecycle = self.spec.lifecycle or NoopTradingLifecycle()
        lifecycle.prepare()
        connections_started = False
        if resources.connections is not None:
            resources.connections.start()
            connections_started = True
        try:
            output = RunOutput(self.spec.run_directory, run_id=self.spec.run_id, mode=self.spec.mode.value)
            projector = RunArtifactProjector(
                output,
                timeline_sample_interval=_timeline_sample_interval(self.spec.normalized_config),
            )
            kernel = RuntimeKernel(
                self.spec.strategy,
                data=resources.data,
                account=resources.account,
                reference=resources.reference,
                trading_execution=resources.trading_execution,
                execution_coordinator=self._execution_coordinator(resources),
            )
            session = RuntimeRunSession(
                run_id=self.spec.run_id,
                mode=self.spec.mode,
                kernel=kernel,
                session=kernel.start(),
            )
            projector.publish_started(session.views)
            result = asyncio.run(_run_with_artifacts(session, resources.source, projector))
            lifecycle.complete()
            return result
        finally:
            if connections_started and resources.connections is not None:
                resources.connections.stop()

    def _execution_coordinator(self, resources: TradingRuntimeResources) -> ExecutionCoordinator | None:
        for candidate in (resources.trading_execution, resources.account):
            coordinator = _coordinator(candidate)
            if coordinator is not None:
                return coordinator
        return None


def _coordinator(candidate: TradingExecutionPort | AccountPort | None) -> ExecutionCoordinator | None:
    value = getattr(candidate, "coordinator", None)
    if isinstance(value, ExecutionCoordinator):
        return value
    return None


def _timeline_sample_interval(config: object) -> object:
    if not isinstance(config, Mapping):
        return "1m"
    timeline = config.get("timeline")
    if not isinstance(timeline, Mapping):
        return "1m"
    return timeline.get("sample_interval", "1m")


async def _run_with_artifacts(
    session: RuntimeRunSession,
    source: RuntimeEventLine | None,
    projector: RunArtifactProjector,
) -> RuntimeRunResult:
    line = source or session.kernel.data or session.kernel.account
    if line is None:
        raise ValueError("runtime event line is required")
    events = RuntimeEnvelopePump(
        line,
        session.mode,
        pre_events=session.pre_events,
        started_at=session.started_at,
    ).events()
    try:
        async for event in events:
            for step in session.session.process(event):
                projector.publish_step(step, session.views)
    finally:
        await close_event_line(events)
    runtime = session.session.finish()
    return RuntimeRunResult(
        run_id=session.run_id,
        mode=session.mode,
        runtime=runtime,
        views=session.views,
        intents=session.intents,
        controls=session.controls,
    )


__all__ = ["TradingSystem"]
