from datetime import datetime, timezone

from kairospy.application.support.messaging import Message


def message(domain: str, kind: str, *, payload: object, sequence: int = 1, at: datetime | None = None, producer: str = "test") -> Message:
    return Message(
        topic=f"{domain}.{kind}",
        payload=payload,
        published_at=at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        producer=producer,
        producer_sequence=sequence,
    )


def topic_message(topic: str, payload: object, *, producer: str, sequence: int = 1) -> Message:
    return Message(
        topic=topic,
        payload=payload,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        producer=producer,
        producer_sequence=sequence,
    )
