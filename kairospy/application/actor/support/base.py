from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from inspect import isawaitable
from typing import Awaitable, Callable

from kairospy.application.support.messaging import Message, MessageBus
from kairospy.application.actor.support.lifecycle import ActorLifecycleEvent, SupervisorLifecycleEvent


@dataclass(slots=True)
class _ActorWork:
    message: Message
    completed: asyncio.Future[None]


class BusinessActor:
    """A stateful application actor with one serial mailbox."""

    def __init__(self, name: str, *, bus: MessageBus | None = None, maxsize: int = 0) -> None:
        if not name.strip():
            raise ValueError("actor name is required")
        if maxsize < 0:
            raise ValueError("actor mailbox maxsize cannot be negative")
        self.name = name
        self.bus = bus
        self.is_finite = False
        self._mailbox: asyncio.Queue[_ActorWork | None] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task[None] | None = None
        self._started = False
        self._stopping = False
        self._event_tasks: list[tuple[asyncio.Task[None], bool]] = []
        self._lifecycle_reporter: Callable[[ActorLifecycleEvent], Awaitable[None] | None] | None = None
        self.error_count = 0
        self.last_error: str | None = None

    def _set_lifecycle_reporter(self, reporter: Callable[[ActorLifecycleEvent], Awaitable[None] | None] | None) -> None:
        self._lifecycle_reporter = reporter

    async def _report_lifecycle(self, state: str, error: BaseException | None = None) -> None:
        reporter = self._lifecycle_reporter
        if reporter is None:
            return
        result = reporter(
            ActorLifecycleEvent(
                actor=self.name,
                state=state,
                at=datetime.now(timezone.utc),
                error=None if error is None else str(error),
            )
        )
        if isawaitable(result):
            await result

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name=f"actor:{self.name}")
        await self.on_start()
        await self._report_lifecycle("started")

    async def handle(self, message: Message) -> None:
        if not self._started or self._task is None:
            raise RuntimeError(f"actor {self.name} is not started")
        if self._stopping:
            raise RuntimeError(f"actor {self.name} is stopping")
        completed = asyncio.get_running_loop().create_future()
        await self._mailbox.put(_ActorWork(message, completed))
        await completed

    async def stop(self) -> None:
        if not self._started:
            return
        self._stopping = True
        await self.on_stop()
        for task, _ in self._event_tasks:
            task.cancel()
        if self._event_tasks:
            await asyncio.gather(*(task for task, _ in self._event_tasks), return_exceptions=True)
        self._event_tasks.clear()
        await self._mailbox.put(None)
        if self._task is not None:
            await self._task
        self._task = None
        self._started = False
        await self._report_lifecycle("stopped")

    async def wait_for_completion(self) -> None:
        """Wait for finite actor-owned event loops."""
        finite = tuple(task for task, is_finite in self._event_tasks if is_finite)
        if finite:
            await asyncio.gather(*finite)
            return
        await asyncio.Event().wait()

    async def on_start(self) -> None:
        """Hook for an actor to start its own data loops."""

    async def on_stop(self) -> None:
        """Hook for an actor to stop actor-owned resources."""

    def start_event_loop(self, events: AsyncIterable[Message], *, is_finite: bool = False, name: str = "events") -> None:
        """Start an actor-owned loop that publishes its events to the bus."""
        if self.bus is None:
            raise RuntimeError(f"actor {self.name} has no message bus")
        task = asyncio.create_task(self._publish_events(events), name=f"actor:{self.name}.{name}")
        self._event_tasks.append((task, is_finite))

    async def _publish_events(self, events: AsyncIterable[Message]) -> None:
        try:
            async for message in events:
                await self.bus.publish(message)  # type: ignore[union-attr]
        finally:
            close = getattr(events, "aclose", None)
            if callable(close):
                await close()

    async def process(self, message: Message) -> None:
        return None

    async def _run(self) -> None:
        while True:
            work = await self._mailbox.get()
            try:
                if work is None:
                    return
                try:
                    await self.process(work.message)
                except BaseException as exc:
                    self.error_count += 1
                    self.last_error = str(exc)
                    await self._report_lifecycle("failed", exc)
                    if not work.completed.done():
                        work.completed.set_exception(exc)
                else:
                    if not work.completed.done():
                        work.completed.set_result(None)
            finally:
                self._mailbox.task_done()


class BusinessActorSupervisor:
    """Own Actor lifecycle and the explicit business topology."""

    def __init__(self, actors: Iterable[BusinessActor] = (), *, monitor: BusinessActor | None = None) -> None:
        self._monitor = monitor
        self._actors = tuple(dict.fromkeys((*(() if monitor is None else (monitor,)), *actors)))
        self._routes: dict[str, list[BusinessActor]] = defaultdict(list)
        self._started = False
        for actor in self._actors:
            if actor is not monitor:
                actor._set_lifecycle_reporter(self._report_actor)  # type: ignore[attr-defined]

    def route(self, topic: str, actor: BusinessActor) -> None:
        if not topic.strip():
            raise ValueError("actor route topic is required")
        if actor not in self._actors:
            self._actors = (*self._actors, actor)
        if actor not in self._routes[topic]:
            self._routes[topic].append(actor)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for actor in self._actors:
            await actor.start()
        await self._report_supervisor("started")

    async def dispatch(self, message: Message) -> None:
        if not self._started:
            raise RuntimeError("business actor supervisor is not started")
        actors = tuple(dict.fromkeys((*self._routes.get(message.topic, ()), *self._routes.get("*", ()))))
        for actor in actors:
            await actor.handle(message)

    async def wait_for_completion(self) -> None:
        if self._actors:
            await asyncio.gather(*(actor.wait_for_completion() for actor in self._actors))

    @property
    def has_finite_actor(self) -> bool:
        return any(getattr(actor, "is_finite", False) for actor in self._actors)

    async def wait_for_finite_completion(self) -> None:
        finite = tuple(actor for actor in self._actors if getattr(actor, "is_finite", False))
        if finite:
            await asyncio.gather(*(actor.wait_for_completion() for actor in finite))

    async def stop(self) -> None:
        if not self._started:
            return
        await self._report_supervisor("stopping")
        managed = tuple(actor for actor in self._actors if actor is not self._monitor)
        for actor in reversed(managed):
            await actor.stop()
        if self._monitor is not None:
            await self._report_supervisor("stopped")
            await self._monitor.stop()
        self._started = False

    async def _report_actor(self, event: ActorLifecycleEvent) -> None:
        if self._monitor is None or not getattr(self._monitor, "_started", False):
            return
        await self._monitor.handle(_lifecycle_message("system.monitor.actor", event, "supervisor"))

    async def _report_supervisor(self, state: str) -> None:
        if self._monitor is None or not getattr(self._monitor, "_started", False):
            return
        event = SupervisorLifecycleEvent(state, datetime.now(timezone.utc), tuple(actor.name for actor in self._actors))
        await self._monitor.handle(_lifecycle_message("system.monitor.supervisor", event, "supervisor"))


def _lifecycle_message(topic: str, payload: object, producer: str) -> Message:
    now = datetime.now(timezone.utc)
    return Message(topic, payload, now, producer, 1)


__all__ = ["BusinessActor", "BusinessActorSupervisor"]
