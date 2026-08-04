from __future__ import annotations

import asyncio

from kairospy.application.support.messaging import SubscriptionClosed
from kairospy.infrastructure.messaging import InMemoryMessageBus
from message_helpers import topic_message


def test_in_memory_bus_delivers_to_the_single_system_inbox() -> None:
    async def scenario() -> None:
        bus = InMemoryMessageBus()
        inbox = bus.open_inbox()

        await bus.publish(topic_message("system.input", {"value": 1}, producer="reference"))

        message = await inbox.receive()
        assert message.payload == {"value": 1}
        assert message.global_sequence == 1
        await bus.close()

    asyncio.run(scenario())


def test_in_memory_bus_keeps_one_inbox_as_an_mpsc_consumer() -> None:
    async def scenario() -> None:
        bus = InMemoryMessageBus()
        inbox = bus.open_inbox(maxsize=8)

        await asyncio.gather(
            bus.publish(topic_message("system.input", "reference", producer="reference")),
            bus.publish(topic_message("system.input", "market", producer="market")),
            bus.publish(topic_message("system.input", "execution", producer="execution")),
        )

        messages = [await inbox.receive() for _ in range(3)]
        assert [message.global_sequence for message in messages] == [1, 2, 3]
        assert {message.payload for message in messages} == {"reference", "market", "execution"}
        await bus.close()

    asyncio.run(scenario())


def test_in_memory_bus_preserves_producer_sequences() -> None:
    async def scenario() -> None:
        bus = InMemoryMessageBus()
        inbox = bus.open_inbox()

        await bus.publish(topic_message("system.input", 1, producer="reference", sequence=7))
        await bus.publish(topic_message("system.input", 2, producer="market", sequence=3))
        await bus.publish(topic_message("system.input", 3, producer="reference", sequence=8))

        messages = [await inbox.receive() for _ in range(3)]
        assert [(message.producer, message.producer_sequence) for message in messages] == [
            ("reference", 7),
            ("market", 3),
            ("reference", 8),
        ]
        await bus.close()

    asyncio.run(scenario())


def test_system_inbox_receives_messages_from_multiple_producers() -> None:
    async def scenario() -> None:
        bus = InMemoryMessageBus()
        inbox = bus.open_inbox()
        await bus.publish(topic_message("reference.catalog.changed", 1, producer="reference"))
        await bus.publish(topic_message("market.quote", 2, producer="market"))
        assert (await inbox.receive()).topic == "reference.catalog.changed"
        assert (await inbox.receive()).topic == "market.quote"
        await inbox.close()
        await bus.close()

    asyncio.run(scenario())


def test_bus_history_is_the_accepted_global_sequence() -> None:
    async def scenario() -> None:
        bus = InMemoryMessageBus()
        await bus.publish(topic_message("market.quote", 1, producer="feed"))
        await bus.publish(topic_message("execution.update", 2, producer="broker"))

        history = bus.history()
        assert [item.global_sequence for item in history] == [1, 2]
        assert [item.producer_sequence for item in history] == [1, 1]
        await bus.close()

    asyncio.run(scenario())


def test_closing_a_full_inbox_is_non_blocking_and_drains_accepted_messages() -> None:
    async def scenario() -> None:
        bus = InMemoryMessageBus()
        inbox = bus.open_inbox(maxsize=1)
        await bus.publish(topic_message("market.quote", "queued", producer="feed"))

        await inbox.close()

        assert (await inbox.receive()).payload == "queued"
        try:
            await inbox.receive()
        except SubscriptionClosed:
            pass
        await bus.close()

    asyncio.run(scenario())


def test_closing_wakes_a_pending_receive() -> None:
    async def scenario() -> None:
        bus = InMemoryMessageBus()
        inbox = bus.open_inbox()
        receive_task = asyncio.create_task(inbox.receive())
        await asyncio.sleep(0)
        await inbox.close()

        try:
            await receive_task
        except SubscriptionClosed:
            pass
        await bus.close()

    asyncio.run(scenario())
