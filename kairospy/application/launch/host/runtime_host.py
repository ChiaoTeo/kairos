from __future__ import annotations

import asyncio
from typing import Mapping

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.orchestration.state import RuntimePorts, RuntimeStores, RuntimeStep
from kairospy.application.ports import AccountPort, TradingExecutionPort
from kairospy.application.runtime.launch import RuntimeLaunchResult, RuntimeLaunchSession
from kairospy.application.runtime.launch.pump import RuntimeEnvelopePump
from kairospy.application.protocol import RuntimeEnvelope, RuntimeEventLine, close_event_line
from kairospy.application.service.runtime import RuntimeApplicationServices, RuntimeServiceDependencies
from kairospy.application.system.artifacts.output import LaunchOutput
from kairospy.application.system.projectors import LaunchArtifactProjector
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.intent import IntentJournal

from .lifecycle import NoopTradingLifecycle, TradingLifecycle
from .resources import TradingRuntimeResources, TradingLaunchSpec


class TradingSystem:
    def __init__(self, spec: TradingLaunchSpec) -> None:
        self.spec = spec

    def run(self) -> RuntimeLaunchResult:
        runtime = self.start()
        try:
            result = asyncio.run(runtime.run(self.spec.resources.source))
            runtime.complete()
            return result
        finally:
            runtime.close()

    def start(self) -> "TradingSystemSession":
        resources = self.spec.resources
        lifecycle = self.spec.lifecycle or NoopTradingLifecycle()
        lifecycle.prepare()
        connections_started = False
        if resources.connections is not None:
            resources.connections.start()
            connections_started = True
        try:
            output = LaunchOutput(self.spec.launch_directory, launch_id=self.spec.launch_id, mode=self.spec.mode.value)
            projector = LaunchArtifactProjector(
                output,
                timeline_sample_interval=_timeline_sample_interval(self.spec.normalized_config),
            )
            coordinator = self._execution_coordinator(resources)
            intents = IntentJournal()
            kernel = RuntimeKernel(
                self.spec.strategy,
                ports=RuntimePorts(
                    data=resources.data,
                    account=resources.account,
                    reference=resources.reference,
                    trading_execution=resources.trading_execution,
                ),
                stores=RuntimeStores(intents=intents),
                services=RuntimeApplicationServices.from_dependencies(
                    RuntimeServiceDependencies(
                        intents=intents,
                        data=resources.data,
                        account_snapshot_store=resources.account,
                        account=resources.account,
                        reference=resources.reference,
                        trading_execution=resources.trading_execution,
                        execution=coordinator,
                        fills_source=resources.trading_execution,
                    )
                ),
            )
            session = RuntimeLaunchSession(
                launch_id=self.spec.launch_id,
                mode=self.spec.mode,
                kernel=kernel,
                session=kernel.start(),
            )
            projector.publish_started(session.views)
            return TradingSystemSession(
                session=session,
                projector=projector,
                source=resources.source,
                lifecycle=lifecycle,
                connections=resources.connections,
                connections_started=connections_started,
            )
        except Exception:
            if connections_started and resources.connections is not None:
                resources.connections.stop()
            raise

    def _execution_coordinator(self, resources: TradingRuntimeResources) -> ExecutionCoordinator | None:
        for candidate in (resources.trading_execution, resources.account):
            coordinator = _coordinator(candidate)
            if coordinator is not None:
                return coordinator
        return None


class TradingSystemSession:
    def __init__(
        self,
        *,
        session: RuntimeLaunchSession,
        projector: LaunchArtifactProjector,
        source: RuntimeEventLine | None,
        lifecycle: TradingLifecycle,
        connections: object | None,
        connections_started: bool,
    ) -> None:
        self.session = session
        self.projector = projector
        self.source = source
        self.lifecycle = lifecycle
        self.connections = connections
        self.connections_started = connections_started
        self._completed = False
        self._closed = False

    @property
    def launch_id(self) -> str:
        return self.session.launch_id

    @property
    def mode(self) -> object:
        return self.session.mode

    @property
    def views(self) -> object:
        return self.session.views

    @property
    def intents(self) -> object:
        return self.session.intents

    @property
    def controls(self) -> object:
        return self.session.controls

    def process(self, event: RuntimeEnvelope) -> tuple[RuntimeStep, ...]:
        steps = self.session.session.process(event)
        for step in steps:
            self.projector.publish_step(step, self.session.views)
        return steps

    async def launch(self, source: RuntimeEventLine | None = None) -> RuntimeLaunchResult:
        return await _launch_with_artifacts(self.session, source or self.source, self.projector)

    async def run(self, source: RuntimeEventLine | None = None) -> RuntimeLaunchResult:
        return await self.launch(source)

    def finish(self) -> RuntimeLaunchResult:
        runtime = self.session.session.finish()
        return RuntimeLaunchResult(
            launch_id=self.session.launch_id,
            mode=self.session.mode,
            runtime=runtime,
            views=self.session.views,
            intents=self.session.intents,
            controls=self.session.controls,
        )

    def complete(self) -> None:
        if not self._completed:
            self.lifecycle.complete()
            self._completed = True

    def close(self) -> None:
        if self._closed:
            return
        if self.connections_started and self.connections is not None:
            self.connections.stop()
        self._closed = True


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


async def _launch_with_artifacts(
    session: RuntimeLaunchSession,
    source: RuntimeEventLine | None,
    projector: LaunchArtifactProjector,
) -> RuntimeLaunchResult:
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
    return RuntimeLaunchResult(
        launch_id=session.launch_id,
        mode=session.mode,
        runtime=runtime,
        views=session.views,
        intents=session.intents,
        controls=session.controls,
    )


__all__ = ["TradingSystem", "TradingSystemSession"]
