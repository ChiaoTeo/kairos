from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairospy.application.support.messaging import Message
from kairospy.application.support.messaging.delivery import MessageDeliveryPolicy


def _message(message_id: str) -> Message:
    return Message(
        topic="market.quote",
        payload={},
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        producer="test",
        producer_sequence=1,
        message_id=message_id,
    )


def test_message_policy_deduplicates_successful_delivery() -> None:
    async def scenario() -> None:
        policy = MessageDeliveryPolicy()
        calls = 0

        async def handler(_event: Message) -> None:
            nonlocal calls
            calls += 1

        assert (await policy.deliver(_message("same"), handler))[0]
        assert not (await policy.deliver(_message("same"), handler))[0]
        assert calls == 1

    asyncio.run(scenario())


def test_message_policy_retries_at_one_central_entrypoint() -> None:
    async def scenario() -> None:
        policy = MessageDeliveryPolicy(max_attempts=2)
        calls = 0

        async def handler(_event: Message) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient")

        assert (await policy.deliver(_message("retry"), handler))[0]
        assert calls == 2

    asyncio.run(scenario())
