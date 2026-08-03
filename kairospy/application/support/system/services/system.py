from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Mapping, Protocol

from kairospy.application.support.runtime.application.engine import RuntimeEngineSpec, RuntimeStores, RuntimeStep, create_runtime_launch_session
from kairospy.application.support.runtime.application.launch import RuntimeLaunchResult, RuntimeLaunchSession
from kairospy.application.support.runtime.application.launch.lifecycle import NoopTradingLifecycle, TradingLifecycle
from kairospy.application.support.runtime.application.launch.pump import RuntimeEnvelopePump
from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.domain.lines import RuntimeEventLine, close_event_line
from kairospy.application.support.system.domain import SystemComponents, SystemIdentity
from kairospy.application.support.system.services.projectors import LaunchArtifactProjector
from kairospy.domain.intent import IntentJournal


class SystemSpec(Protocol):
    launch_id: str
    mode: object
    strategy: object
    resources: object
    launch_directory: object
    normalized_config: Mapping[str, object]
    lifecycle: TradingLifecycle | None


@dataclass(slots=True)
class TradingSystem:
    """Runtime root for one composed trading system instance.

    System directly owns the runtime session. Composition supplies resources;
    no runtime factory indirection is needed for the current architecture.
    """

    spec: SystemSpec
    _session: TradingSystemSession | None = None

    @property
    def identity(self) -> SystemIdentity:
        return SystemIdentity(self.spec.launch_id, self.spec.mode)

    @property
    def components(self) -> SystemComponents:
        components = SystemComponents.from_runtime(self.spec.resources.runtime_components())
        return SystemComponents(
            market=components.market,
            account=components.account,
            account_catalog=components.account_catalog,
            execution=components.execution,
            reference=components.reference,
            strategy=self.spec.strategy,
        )

    def start(self) -> "TradingSystemSession":
        if self._session is not None:
            return self._session
        resources = self.spec.resources
        lifecycle = self.spec.lifecycle or NoopTradingLifecycle()
        connections_started = False
        if resources.connection_scope is not None:
            resources.connection_scope.start()
            connections_started = True
        try:
            lifecycle.prepare()
            assembly = getattr(resources, "assembly", None)
            if assembly is None:
                raise RuntimeError("TradingSystem requires a runtime assembly supplied by composition")
            output = assembly.output(self.spec.launch_directory, launch_id=self.spec.launch_id, mode=self.spec.mode.value)
            projector = LaunchArtifactProjector(output, timeline_sample_interval=_timeline_sample_interval(self.spec.normalized_config))
            stores = RuntimeStores(intents=IntentJournal())
            components = self.components.runtime_components()
            services = assembly.services(components, stores)
            runtime_session = create_runtime_launch_session(
                RuntimeEngineSpec(
                    launch_id=self.spec.launch_id,
                    mode=self.spec.mode,
                    strategy=self.spec.strategy,
                    components=components,
                    stores=stores,
                    processors=assembly.projectors(self.spec.strategy.strategy_id, stores.intents, services),
                )
            )
            projector.publish_started(runtime_session.views)
            _publish_connection_health(projector, resources.connection_scope)
            self._session = TradingSystemSession(
                system=self,
                session=runtime_session,
                projector=projector,
                source=resources.source,
                lifecycle=lifecycle,
                connections=resources.connection_scope,
                connections_started=connections_started,
            )
            return self._session
        except Exception:
            if connections_started and resources.connection_scope is not None:
                resources.connection_scope.stop()
            raise

    def run(self) -> RuntimeLaunchResult:
        session = self.start()
        try:
            result = asyncio.run(session.run())
            session.complete()
            return result
        finally:
            session.close()

    def process(self, event: RuntimeEnvelope) -> tuple[RuntimeStep, ...]:
        return self.start().process(event)

class TradingSystemSession:
    """Started runtime session owned by ``TradingSystem``."""

    def __init__(self, *, system: TradingSystem, session: RuntimeLaunchSession, projector: LaunchArtifactProjector, source: RuntimeEventLine | None, lifecycle: TradingLifecycle, connections: object | None, connections_started: bool) -> None:
        self.system = system
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
    def views(self) -> ViewStore:
        return self.session.views

    @property
    def intents(self) -> IntentJournal:
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
        return RuntimeLaunchResult(launch_id=self.session.launch_id, mode=self.session.mode, runtime=runtime, views=self.session.views, intents=self.session.intents)

    def complete(self) -> None:
        if not self._completed:
            self.lifecycle.complete()
            self._completed = True

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self.connections_started and self.connections is not None:
                self.connections.stop()
                _publish_connection_health(self.projector, self.connections)
        finally:
            self._closed = True


def _timeline_sample_interval(config: object) -> object:
    if not isinstance(config, Mapping):
        return "1m"
    timeline = config.get("timeline")
    return timeline.get("sample_interval", "1m") if isinstance(timeline, Mapping) else "1m"


async def _launch_with_artifacts(session: RuntimeLaunchSession, source: RuntimeEventLine | None, projector: LaunchArtifactProjector) -> RuntimeLaunchResult:
    line = source or session.kernel.data or session.kernel.account
    if line is None:
        raise ValueError("runtime event line is required")
    events = RuntimeEnvelopePump(line, session.mode, pre_events=session.pre_events, started_at=session.started_at).events()
    try:
        async for event in events:
            for step in session.session.process(event):
                projector.publish_step(step, session.views)
    finally:
        await close_event_line(events)
    runtime = session.session.finish()
    return RuntimeLaunchResult(launch_id=session.launch_id, mode=session.mode, runtime=runtime, views=session.views, intents=session.intents)


def _publish_connection_health(projector: LaunchArtifactProjector, connections: object | None) -> None:
    health = getattr(connections, "health", None)
    if callable(health):
        projector.publish_connection_health(health())


__all__ = ["TradingSystem", "TradingSystemSession"]
