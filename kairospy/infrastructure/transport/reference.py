"""Reference v2 event transport adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import struct
from typing import Any

from kairospy.application.reference.events import ReferenceEventRecord
from kairospy.infrastructure.contracts.reference import decode_event
from kairospy.infrastructure.transport.aeron_bridge import check_aeron_bridge


class AeronReferenceEventSource:
    """Consume one Reference v2 FlatBuffer event per Aeron frame."""

    join_from_latest = True

    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = "aeron:udp?endpoint=localhost:40123",
        stream_id: int = 1201,
        binary: str,
    ) -> None:
        self.aeron_dir = None if aeron_dir is None else str(aeron_dir)
        self.channel = channel
        self.stream_id = stream_id
        self.binary = binary

    def _command(self) -> list[str]:
        command = [
            self.binary,
            "--aeron-channel",
            self.channel,
            "--stream-id",
            str(self.stream_id),
        ]
        if self.aeron_dir is not None:
            command.extend(("--aeron-dir", self.aeron_dir))
        return command

    def check_ready(self) -> None:
        check_aeron_bridge(self._command(), domain="Reference")

    async def events(
        self, after_sequence: int = 0
    ) -> AsyncIterator[ReferenceEventRecord]:
        process = await asyncio.create_subprocess_exec(
            *self._command(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        try:
            while True:
                try:
                    size = struct.unpack(">I", await process.stdout.readexactly(4))[0]
                    if size == 0 or size > 4 * 1024 * 1024:
                        raise ValueError("invalid Reference event frame length")
                    payload = await process.stdout.readexactly(size)
                except asyncio.IncompleteReadError:
                    break
                record = decode_reference_event(payload)
                if record.sequence > after_sequence:
                    yield record
            status = await process.wait()
            if status != 0:
                assert process.stderr is not None
                error = (await process.stderr.read()).decode(errors="replace").strip()
                raise RuntimeError(
                    error or f"Reference Aeron bridge exited with {status}"
                )
            raise RuntimeError("Reference Aeron bridge ended unexpectedly")
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()


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
