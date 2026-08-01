from __future__ import annotations

import asyncio
from typing import Mapping

from kairospy.application.support.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.support.runtime.orchestration.state import RuntimeStores, RuntimeStep
from kairospy.application.support.runtime.components import RuntimeComponents
from kairospy.application.support.runtime.launch import RuntimeLaunchResult, RuntimeLaunchSession
from kairospy.application.support.runtime.launch.pump import RuntimeEnvelopePump
from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.support.runtime.lines import RuntimeEventLine, close_event_line
from kairospy.application.support.runtime.services.application import RuntimeApplicationServices, RuntimeServiceDependencies
from kairospy.application.support.launch.composition.artifacts import launch_output
from kairospy.application.support.system.projectors import LaunchArtifactProjector
from kairospy.core.intent import IntentJournal

from .lifecycle import NoopTradingLifecycle, TradingLifecycle
from .resources import TradingLaunchSpec


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
        components = resources.runtime_components()
        lifecycle = self.spec.lifecycle or NoopTradingLifecycle()
        lifecycle.prepare()
        connections_started = False
        if resources.connections is not None:
            resources.connections.start()
            connections_started = True
        try:
            output = launch_output(self.spec.launch_directory, launch_id=self.spec.launch_id, mode=self.spec.mode.value)
            projector = LaunchArtifactProjector(
                output,
                timeline_sample_interval=_timeline_sample_interval(self.spec.normalized_config),
            )
            coordinator = self._execution_coordinator(components)
            intents = IntentJournal()
            kernel = RuntimeKernel(
                self.spec.strategy,
                components=components,
                stores=RuntimeStores(intents=intents),
                services=RuntimeApplicationServices.from_dependencies(
                    RuntimeServiceDependencies(
                        intents=intents,
                        data=components.market,
                        account_snapshot_store=components.account,
                        account=components.account,
                        account_catalog=components.account_catalog,
                        account_directory=_account_directory(components),
                        reference=components.reference,
                        trading_execution=components.execution,
                        execution_coordinator=coordinator,
                        fills_source=components.execution,
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
            _publish_connection_health(projector, resources.connections)
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

    def _execution_coordinator(self, components: RuntimeComponents) -> object | None:
        for candidate in (components.execution, components.account):
            coordinator = getattr(candidate, "coordinator", None)
            if coordinator is not None:
                return coordinator
        return None


def _account_directory(components: RuntimeComponents):
    catalog = components.account_catalog
    provider = getattr(catalog, "directory", None)
    return provider() if callable(provider) else None


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
            _publish_connection_health(self.projector, self.connections)
        self._closed = True


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
    )


def _publish_connection_health(projector: LaunchArtifactProjector, connections: object | None) -> None:
    health = getattr(connections, "health", None)
    if callable(health):
        projector.publish_connection_health(health())


__all__ = ["TradingSystem", "TradingSystemSession"]
