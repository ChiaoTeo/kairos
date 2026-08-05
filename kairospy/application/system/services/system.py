from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Mapping, Protocol

from kairospy.application.support.runtime.application.engine import RuntimeCycle, RuntimeEngineSpec, RuntimeStores, create_runtime_session
from kairospy.application.support.launch.application.runtime import LaunchRuntimeResult, LaunchRuntimeSession
from kairospy.application.support.launch.application.lifecycle import NoopTradingLifecycle, TradingLifecycle
from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.messaging import Message, SubscriptionClosed
from kairospy.application.support.runtime.application.interaction import SystemCallResult
from kairospy.application.support.runtime.domain.commands import RuntimeCommand
from kairospy.application.support.launch.domain.identity import LaunchIdentity
from kairospy.application.system.protocol import SystemBusinessRuntime
from kairospy.application.usecases.strategy.application.runtime import build_strategy_dispatcher


_LOGGER = logging.getLogger("kairospy.system")


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
    _business: SystemBusinessRuntime | None = None

    @property
    def identity(self) -> LaunchIdentity:
        return LaunchIdentity(self.spec.launch_id, self.spec.mode)

    def start(self) -> "TradingSystemSession":
        if self._session is not None:
            return self._session
        _LOGGER.info("system=%s phase=starting mode=%s", self.spec.launch_id, getattr(self.spec.mode, "value", self.spec.mode))
        resources = self.spec.resources
        lifecycle = self.spec.lifecycle or NoopTradingLifecycle()
        try:
            lifecycle.prepare()
            _LOGGER.info("system=%s phase=lifecycle_prepared", self.spec.launch_id)
            assembly = getattr(resources, "assembly", None)
            if assembly is None:
                raise RuntimeError("TradingSystem requires a runtime assembly supplied by composition")
            output = assembly.output(self.spec.launch_directory, launch_id=self.spec.launch_id, mode=self.spec.mode.value)
            business_factory = getattr(resources, "business", None)
            if business_factory is None or not callable(getattr(business_factory, "start", None)):
                raise RuntimeError("TradingSystem requires a business application supplied by composition")
            stores = RuntimeStores()
            business = business_factory.start(
                resources=resources,
                strategy_id=self.spec.strategy.strategy_id,
                artifact_output=output,
                timeline_sample_interval=_timeline_sample_interval(self.spec.normalized_config),
                normalized_config=self.spec.normalized_config,
                message_bus=getattr(resources, "message_bus", None),
            )
            _LOGGER.info("system=%s phase=business_composed", self.spec.launch_id)
            self._business = business
            business.attach(views=stores.views)
            runtime_session = create_runtime_session(
                RuntimeEngineSpec(
                    program_id=self.spec.strategy.strategy_id,
                    dispatcher_factory=lambda **kwargs: build_strategy_dispatcher(self.spec.strategy, **kwargs),
                    system_call=business,
                    stores=stores,
                    reference=resources.reference,
                )
            )
            _LOGGER.info("system=%s phase=strategy_on_start_completed", self.spec.launch_id)
            runtime_session = LaunchRuntimeSession(
                launch_id=self.spec.launch_id,
                mode=self.spec.mode,
                session=runtime_session,
            )
            business.bind_runtime(runtime_session)
            _LOGGER.info("system=%s phase=runtime_bound strategy=%s", self.spec.launch_id, self.spec.strategy.strategy_id)
            self._session = TradingSystemSession(
                system=self,
                session=runtime_session,
                business=business,
                lifecycle=lifecycle,
            )
            return self._session
        except Exception:
            raise

    def run(self) -> LaunchRuntimeResult:
        session = self.start()
        try:
            result = asyncio.run(session.run())
            session.complete()
            return result
        finally:
            session.close()

    def process(self, event: Message) -> tuple[RuntimeCycle, ...]:
        return self.start().process(event)

    def step(self, event: Message) -> tuple[RuntimeCycle, ...]:
        """Process one externally supplied runtime input.

        ``run`` owns the long-lived source loop; ``step`` is the explicit
        single-cycle System entrypoint used by deterministic hosts and tests.
        """
        return self.start().step(event)

    def call(self, command: RuntimeCommand) -> SystemCallResult:
        """Enter the System synchronously from a strategy execution.

        Runtime owns the execution frame, while System owns the meaning and
        lifecycle of the command.  The call returns before the strategy
        resumes; no command is staged in Runtime for a later batch flush.
        """
        return self.start().call(command)

    def stop(self) -> None:
        """Stop the runtime frame; Actor business owns connection release."""
        session = self._session
        if session is None:
            return
        session.stop()
        _LOGGER.info("system=%s phase=stopped", self.spec.launch_id)
        if self._business is not None:
            self._business.detach()
        self._session = None
        self._business = None

    @property
    def intents(self) -> object:
        if self._business is None:
            raise RuntimeError("system has not started")
        return self._business.intents

class TradingSystemSession:
    """Started runtime session owned by ``TradingSystem``."""

    def __init__(self, *, system: TradingSystem, session: LaunchRuntimeSession, business: SystemBusinessRuntime, lifecycle: TradingLifecycle) -> None:
        self.system = system
        self.session = session
        self.business = business
        self.lifecycle = lifecycle
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
    def intents(self) -> object:
        return self.business.intents

    @property
    def started_at(self) -> object | None:
        return self.session.started_at

    def process(self, event: Message) -> tuple[RuntimeCycle, ...]:
        return self.business.process(event)  # type: ignore[return-value]

    async def process_async(self, event: Message) -> tuple[RuntimeCycle, ...]:
        return await self.business.process_async(event)  # type: ignore[return-value]

    def step(self, event: Message) -> tuple[RuntimeCycle, ...]:
        return self.process(event)

    def call(self, command: RuntimeCommand) -> SystemCallResult:
        return self.business.call(command)

    async def launch(self) -> LaunchRuntimeResult:
        return await _launch_with_artifacts(self)

    async def run(self) -> LaunchRuntimeResult:
        return await self.launch()

    def finish(self) -> LaunchRuntimeResult:
        runtime = self.session.finish()
        return LaunchRuntimeResult(launch_id=self.session.launch_id, mode=self.session.mode, runtime=runtime, views=self.session.views, intents=self.business.intents)

    def complete(self) -> None:
        if not self._completed:
            self.lifecycle.complete()
            self._completed = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.system._business is not None:
            self.system._business.detach()
        self.system._session = None
        self.system._business = None

    def stop(self) -> None:
        """Stop this session exactly once and release System-owned resources."""
        self.session.stop()
        self.close()


def _timeline_sample_interval(config: object) -> object:
    if not isinstance(config, Mapping):
        return "1m"
    timeline = config.get("timeline")
    return timeline.get("sample_interval", "1m") if isinstance(timeline, Mapping) else "1m"


async def _launch_with_artifacts(session: TradingSystemSession) -> LaunchRuntimeResult:
    subscription = session.business.message_inbox()
    if subscription is None:
        raise ValueError("SystemBusinessRuntime must expose a MessageBus subscription")
    _LOGGER.info("system=%s phase=actors_starting", session.launch_id)
    await session.business.start_actors()
    _LOGGER.info("system=%s phase=actors_started", session.launch_id)
    input_streams = tuple(getattr(session.system.spec.resources, "input_streams", ()) or ())
    input_tasks = tuple(
        asyncio.create_task(_publish_input_stream(stream, session.business), name=f"system:input:{index}")
        for index, stream in enumerate(input_streams)
    )
    finite_input_tasks = tuple(
        task for task, stream in zip(input_tasks, input_streams)
        if bool(getattr(stream, "is_finite", False))
    )
    completion_task = (
        asyncio.create_task(_wait_for_completion(session.business, finite_input_tasks))
        if session.business.has_finite_actors or finite_input_tasks
        else None
    )
    receive_task = asyncio.create_task(subscription.receive())
    try:
        while True:
            pending = {receive_task}
            if completion_task is not None:
                pending.add(completion_task)
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if completion_task is not None and completion_task in done:
                completion_task.result()
                completion_task = None
                if receive_task in done:
                    try:
                        await session.process_async(receive_task.result())
                    except SubscriptionClosed:
                        break
                    receive_task = asyncio.create_task(subscription.receive())
                await subscription.close()
                continue
            try:
                event = receive_task.result()
            except SubscriptionClosed:
                break
            await session.process_async(event)
            receive_task = asyncio.create_task(subscription.receive())
    finally:
        receive_task.cancel()
        await asyncio.gather(receive_task, return_exceptions=True)
        if completion_task is not None:
            completion_task.cancel()
            await asyncio.gather(completion_task, return_exceptions=True)
        for task in input_tasks:
            task.cancel()
        if input_tasks:
            await asyncio.gather(*input_tasks, return_exceptions=True)
        await session.business.stop_actors()
        await session.business.close()
        _LOGGER.info("system=%s phase=actors_stopped", session.launch_id)
    return session.finish()


async def _publish_input_stream(stream: object, business: object) -> None:
    events = getattr(stream, "events", None)
    if not callable(events):
        return
    iterator = events()
    try:
        async for event in iterator:
            await getattr(business, "message_bus").publish(event)
    finally:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            await close()


async def _wait_for_completion(business: object, finite_input_tasks: tuple[asyncio.Task[None], ...]) -> None:
    waits = []
    if getattr(business, "has_finite_actors", False):
        waits.append(getattr(business, "wait_for_finite_actors")())
    waits.extend(finite_input_tasks)
    if waits:
        await asyncio.gather(*waits)


__all__ = ["TradingSystem", "TradingSystemSession"]
