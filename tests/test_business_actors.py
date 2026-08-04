from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairospy.application.actor import BusinessActor, BusinessActorSupervisor
from kairospy.application.actor.monitor import MonitorActor
from kairospy.application.support.messaging import Message
from kairospy.application.support.launch.application.sources import IterableEventSource
from kairospy.infrastructure.messaging import InMemoryMessageBus
from message_helpers import message as make_message


class RecordingActor(BusinessActor):
    def __init__(self, name: str, record: list[str]) -> None:
        super().__init__(name)
        self.record = record

    async def process(self, message: Message) -> None:
        self.record.append(f"{self.name}:{message.payload}")


def _message(topic: str, payload: str, sequence: int) -> Message:
    domain, kind = topic.split(".", 1)
    return make_message(domain, kind, at=datetime(2026, 1, 1, tzinfo=timezone.utc), sequence=sequence, payload=payload, producer="test")


def test_actor_processes_messages_serially_and_dispatch_waits_for_completion() -> None:
    async def scenario() -> None:
        record: list[str] = []
        actor = RecordingActor("reference", record)
        supervisor = BusinessActorSupervisor([actor])
        supervisor.route("reference.catalog.changed", actor)
        await supervisor.start()

        await asyncio.gather(
            supervisor.dispatch(_message("reference.catalog.changed", "one", 1)),
            supervisor.dispatch(_message("reference.catalog.changed", "two", 2)),
        )

        assert record == ["reference:one", "reference:two"] or record == ["reference:two", "reference:one"]
        await supervisor.stop()

    asyncio.run(scenario())


def test_supervisor_can_route_one_message_to_multiple_actors() -> None:
    async def scenario() -> None:
        record: list[str] = []
        reference = RecordingActor("reference", record)
        projector = RecordingActor("projector", record)
        supervisor = BusinessActorSupervisor([reference, projector])
        supervisor.route("reference.catalog.changed", reference)
        supervisor.route("reference.catalog.changed", projector)
        await supervisor.start()

        await supervisor.dispatch(_message("reference.catalog.changed", "changed", 1))

        assert record == ["reference:changed", "projector:changed"]
        await supervisor.stop()

    asyncio.run(scenario())


def test_actor_owns_a_finite_event_loop() -> None:
    async def scenario() -> None:
        bus = InMemoryMessageBus()
        subscription = bus.open_inbox()
        source = IterableEventSource("backtest", [_message("market.tick", "tick", 1)])
        class PublishingActor(BusinessActor):
            def __init__(self) -> None:
                super().__init__("market", bus=bus)
                self.is_finite = True

            async def on_start(self) -> None:
                self.start_event_loop(source.events(), is_finite=True)

        actor = PublishingActor()
        supervisor = BusinessActorSupervisor([actor])
        await supervisor.start()

        await supervisor.wait_for_finite_completion()

        assert (await subscription.receive()).payload == "tick"
        await supervisor.stop()
        await subscription.close()
        await bus.close()

    asyncio.run(scenario())


def test_monitor_actor_receives_actor_and_supervisor_lifecycle_events() -> None:
    async def scenario() -> None:
        monitor = MonitorActor(strategy_id="monitor-test")
        actor = RecordingActor("market", [])
        supervisor = BusinessActorSupervisor([actor], monitor=monitor)
        supervisor.route("market.tick", actor)
        await supervisor.start()

        assert {item.actor: item.state for item in monitor.actors()}["market"] == "started"
        assert monitor.supervisor is not None
        assert monitor.supervisor.state == "started"

        await supervisor.stop()

        assert {item.actor: item.state for item in monitor.actors()}["market"] == "stopped"
        assert monitor.supervisor is not None
        assert monitor.supervisor.state == "stopped"

    asyncio.run(scenario())
