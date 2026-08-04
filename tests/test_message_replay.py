from __future__ import annotations

import asyncio

from kairospy.infrastructure.messaging import InMemoryMessageBus, replay_messages
from message_helpers import topic_message


def test_replay_drives_messages_by_recorded_global_sequence() -> None:
    async def scenario() -> None:
        bus = InMemoryMessageBus()
        inbox = bus.open_inbox()
        await bus.publish(topic_message("market.quote", "second", producer="market"))
        await bus.publish(topic_message("execution.update", "first", producer="execution"))
        history = bus.history()
        await inbox.close()
        await bus.close()

        received: list[str] = []

        async def consumer(message) -> None:
            received.append(str(message.payload))

        await replay_messages(history, consumer)
        assert received == ["second", "first"]

    asyncio.run(scenario())
