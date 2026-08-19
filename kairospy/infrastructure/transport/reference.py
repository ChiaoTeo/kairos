"""Reference v2 event transport adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kairospy.application.reference.events import ReferenceEventRecord
from kairospy.infrastructure.contracts.reference.events import decode_event
from kairospy.infrastructure.transport.native_event import NativeEventSource
from kairospy.infrastructure.transport.generated_spec import (
    DEFAULT_CHANNEL,
    REFERENCE_CHANGES,
)


class AeronReferenceEventSource(NativeEventSource[ReferenceEventRecord]):
    """Consume one Reference v2 FlatBuffer event per Aeron frame."""

    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = DEFAULT_CHANNEL,
        stream_id: int = REFERENCE_CHANGES,
    ) -> None:
        super().__init__(
            decoder=decode_reference_event,
            aeron_dir=aeron_dir,
            channel=channel,
            stream_id=stream_id,
        )


def decode_reference_event(payload: bytes) -> ReferenceEventRecord:
    root = decode_event(payload)
    metadata = root.Metadata()
    if metadata is None:
        raise ValueError("Reference event metadata is missing")
    sequence = int(metadata.Sequence())
    if sequence <= 0:
        raise ValueError("Reference event sequence must be positive")
    event_id = _required_text(metadata.EventId(), "event_id")
    stream_id = _required_text(metadata.StreamId(), "stream_id")
    producer = _required_text(metadata.ProducerId(), "producer_id")
    root_name = type(root).__name__
    return ReferenceEventRecord(
        event_id=event_id,
        stream_id=stream_id,
        sequence=sequence,
        producer=producer,
        kind=_event_kind(root_name),
        catalog_revision=int(root.CatalogRevision()),
        occurred_at_unix_nanos=int(metadata.OccurredAtUnixNanos()),
        payload=root,
        launch_id=_optional_text(metadata.LaunchId()),
        instance_id=_optional_text(metadata.InstanceId()),
    )


def _event_kind(root_name: str) -> str:
    if not root_name.endswith(("Upserted", "Updated")):
        raise ValueError(f"unsupported Reference v2 event root: {root_name}")
    suffix = "_upserted" if root_name.endswith("Upserted") else "_updated"
    stem = root_name[: -len("Upserted" if suffix == "_upserted" else "Updated")]
    return (
        "".join(
            (f"_{char.lower()}" if char.isupper() else char) for char in stem
        ).lstrip("_")
        + suffix
    )


def _required_text(value: Any, field: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"Reference event {field} is missing")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode()
    return str(value) if value else None


__all__ = ["AeronReferenceEventSource", "decode_reference_event"]
