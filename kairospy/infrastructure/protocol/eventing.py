"""Lease-neutral process mechanics shared by owner event contracts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from kairospy.primitives.runtime import (
    EventIdRead,
    InstanceIdRead,
    LaunchIdRead,
    ProducerIdRead,
    WorkspaceIdRead,
)
from kairospy.primitives.time import SequenceRead, UnixNanosRead


class EventMetadataRead(Protocol):
    @property
    def event_id(self) -> EventIdRead: ...

    @property
    def stream_id(self) -> str: ...

    @property
    def sequence(self) -> SequenceRead: ...

    @property
    def producer(self) -> ProducerIdRead: ...

    @property
    def producer_incarnation(self) -> int: ...

    @property
    def workspace_id(self) -> WorkspaceIdRead: ...

    @property
    def launch_id(self) -> LaunchIdRead | None: ...

    @property
    def instance_id(self) -> InstanceIdRead | None: ...

    @property
    def correlation_id(self) -> EventIdRead | None: ...

    @property
    def causation_id(self) -> EventIdRead | None: ...

    @property
    def occurred_at_unix_nanos(self) -> UnixNanosRead: ...

    @property
    def published_at_unix_nanos(self) -> UnixNanosRead: ...


TData_co = TypeVar("TData_co", covariant=True)


class BusinessEventRead(Protocol[TData_co]):
    @property
    def metadata(self) -> EventMetadataRead: ...

    @property
    def kind(self) -> str: ...

    @property
    def data(self) -> TData_co: ...


TEvent_co = TypeVar("TEvent_co", covariant=True)


class LiveEventSource(Protocol[TEvent_co]):
    """Single-threaded callback-scoped owner event source."""

    def poll_visit(
        self,
        visitor: Callable[[TEvent_co], None],
        *,
        fragment_limit: int = 64,
    ) -> int: ...

    def close(self) -> None: ...


__all__ = ["BusinessEventRead", "EventMetadataRead", "LiveEventSource"]
