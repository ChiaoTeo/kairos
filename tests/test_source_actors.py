from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairospy.application.actor.account.application import AccountActor
from kairospy.application.support.messaging import Message
from message_helpers import message as make_message
from kairospy.application.support.launch.application.sources import IterableEventSource
from kairospy.infrastructure.messaging import InMemoryMessageBus


def test_account_actor_owns_account_event_loop() -> None:
    async def scenario() -> None:
        event = make_message("account", "snapshot", at=datetime(2026, 1, 1, tzinfo=timezone.utc), sequence=1, payload={}, producer="test")
        bus = InMemoryMessageBus()
        subscription = bus.open_inbox()
        actor = AccountActor(
            IterableEventSource("test", [event]),
            bus,
        )
        await actor.start()
        assert (await asyncio.wait_for(subscription.receive(), timeout=1)).payload == event.payload
        await actor.stop()
        await bus.close()

    asyncio.run(scenario())


def test_account_actor_owns_execution_event_loop() -> None:
    async def scenario() -> None:
        event = make_message("execution", "update", at=datetime(2026, 1, 1, tzinfo=timezone.utc), sequence=1, payload={}, producer="test")
        bus = InMemoryMessageBus()
        subscription = bus.open_inbox()
        actor = AccountActor(None, bus, execution_source=IterableEventSource("test", [event]))
        await actor.start()
        assert (await asyncio.wait_for(subscription.receive(), timeout=1)).payload == event.payload
        await actor.stop()
        await bus.close()

    asyncio.run(scenario())
