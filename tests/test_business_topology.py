from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairospy.application.support.messaging import Message
from kairospy.application.support.messaging.topology import MessageTopology


def test_business_topology_dispatches_exact_domain_and_wildcard_routes_in_order() -> None:
    async def scenario() -> None:
        topology = MessageTopology()
        received: list[str] = []

        def exact(message: Message) -> None:
            received.append(f"exact:{message.topic}")

        async def domain(message: Message) -> None:
            received.append(f"domain:{message.topic}")

        def all_messages(message: Message) -> None:
            received.append(f"all:{message.topic}")

        topology.register("execution.order.filled", exact)
        topology.register("execution.*", domain)
        topology.register("*", all_messages)

        await topology.dispatch(
            Message(
                topic="execution.order.filled",
                payload={},
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                producer="test",
                producer_sequence=1,
            )
        )

        assert received == [
            "exact:execution.order.filled",
            "domain:execution.order.filled",
            "all:execution.order.filled",
        ]

    asyncio.run(scenario())
