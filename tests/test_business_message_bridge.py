from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairospy.application.support.messaging import Message
from message_helpers import message as make_message
from kairospy.infrastructure.messaging import InMemoryMessageBus


def test_bus_delivers_business_messages_to_the_single_system_consumer() -> None:
    async def scenario() -> None:
        event = make_message("reference", "catalog.changed", at=datetime(2026, 1, 1, tzinfo=timezone.utc), sequence=1, payload={}, producer="test")
        bus = InMemoryMessageBus()
        subscription = bus.open_inbox()
        await bus.publish(event)
        assert (await subscription.receive()).topic == event.topic
        await subscription.close()
        await bus.close()

    asyncio.run(scenario())
