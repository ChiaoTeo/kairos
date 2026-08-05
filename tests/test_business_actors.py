from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from kairospy.application.actor import BusinessActor, BusinessActorSupervisor
from kairospy.application.actor.monitor import MonitorActor
from kairospy.application.actor.support.lifecycle import ActorLifecycleEvent
from kairospy.application.support.messaging import Message
from kairospy.application.support.launch.application.sources import IterableEventSource
from kairospy.infrastructure.messaging import InMemoryMessageBus
from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.domain.intent import target_position_intent
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


def test_supervisor_domain_route_does_not_consume_unrelated_domains() -> None:
    async def scenario() -> None:
        record: list[str] = []
        market = RecordingActor("market", record)
        supervisor = BusinessActorSupervisor([market])
        supervisor.route_domain("market", market)
        await supervisor.start()

        await supervisor.dispatch(_message("market.quote", "quote", 1))
        await supervisor.dispatch(_message("execution.update", "fill", 2))

        assert record == ["market:quote"]
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


def test_monitor_projects_health_freshness_and_operation_chain() -> None:
    async def scenario() -> None:
        monitor = MonitorActor(strategy_id="monitor-health")
        actor = RecordingActor("market", [])
        monitor.bind_actor_sources((actor,))
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        intent = target_position_intent(
            strategy_id="monitor-health",
            instrument_id="BTCUSDT",
            target_quantity=Decimal("1"),
            at=at,
            intent_id="monitor-intent",
        )
        await actor.start()
        await monitor.start()
        try:
            await actor.handle(_message("market.quote", "quote", 1))
            await monitor.handle(_message("market.quote", "quote", 1))
            await monitor.handle(Message("execution.update", SimpleNamespace(order_id="order-1", status="filled"), at, "execution", 1))
            monitor.record_connection_health({"items": ({"connection": "paper", "status": "ready", "healthy": True},)})
            monitor.record_intents((intent,), SimpleNamespace(now=at), "on_quote")
            views = ViewStore()
            monitor.projectors.register_views(views)
            monitor.projectors.publish_views(views, as_of=at)

            health = views.require("system.health")
            assert health.event_count == 2
            assert health.actors[0].processed_count == 1
            assert health.connections[0].connection == "paper"
            assert views.require("system.freshness").last_market_event_time == at
            operations = views.require("system.operations").operations
            assert {item.operation_id for item in operations} == {"monitor-intent", "order-1"}
        finally:
            await actor.stop()
            await monitor.stop()

    asyncio.run(scenario())


def test_monitor_raises_and_resolves_runtime_alerts() -> None:
    async def scenario() -> None:
        monitor = MonitorActor(strategy_id="monitor-alerts", freshness_stale_after=timedelta(minutes=5))
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await monitor.start()
        try:
            await monitor.handle(Message("market.quote", "quote", at, "market", 1))
            await monitor.handle(Message(
                "system.monitor.actor",
                ActorLifecycleEvent("market", "failed", at, "feed disconnected"),
                at,
                "supervisor",
                1,
            ))
            later = at.replace(minute=10)
            await monitor.handle(Message("system.heartbeat", {}, later, "system", 1))
            views = ViewStore()
            monitor.projectors.register_views(views)
            monitor.projectors.publish_views(views, as_of=later)
            alerts = views.require("system.alerts").alerts
            active = {item.alert_id: item for item in alerts if item.status == "active"}
            assert "actor_failed:market" in active
            assert "freshness_stale:market" in active
            assert {topic for topic, _ in monitor.projectors.drain_alert_facts()} == {"monitor.alert.raised"}

            await monitor.handle(Message(
                "system.monitor.actor",
                ActorLifecycleEvent("market", "started", later),
                later,
                "supervisor",
                2,
            ))
            monitor.projectors.publish_views(views, as_of=later)
            resolved = {item.alert_id: item for item in views.require("system.alerts").alerts if item.status == "resolved"}
            assert resolved["actor_failed:market"].resolved_at == later
        finally:
            await monitor.stop()

    asyncio.run(scenario())
