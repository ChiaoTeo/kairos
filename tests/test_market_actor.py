from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairospy.application.actor.market.application import MarketActor
from kairospy.application.support.messaging import Message
from message_helpers import message as make_message
from kairospy.application.support.launch.application.sources import IterableEventSource
from kairospy.infrastructure.messaging import InMemoryMessageBus


def test_market_actor_owns_source_and_publishes_to_bus() -> None:
    async def scenario() -> None:
        event = make_message("market", "quote", at=datetime(2026, 1, 1, tzinfo=timezone.utc), sequence=1, payload={"symbol": "BTC"}, producer="test")
        bus = InMemoryMessageBus()
        subscription = bus.open_inbox()
        actor = MarketActor(
            IterableEventSource("test", [event]),
            bus,
        )
        await actor.start()
        message = await asyncio.wait_for(subscription.receive(), timeout=1)
        assert message.topic == "market.quote"
        assert message.payload == event.payload
        await actor.stop()
        await bus.close()

    asyncio.run(scenario())
