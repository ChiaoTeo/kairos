"""Read-only Strategy view of the owner-native Account event contract."""

from __future__ import annotations

from typing import Protocol


class AccountEventMetadata(Protocol):
    stream_id: str
    sequence: int
    producer: str
    occurred_at_unix_nanos: int


class AccountEventChange(Protocol):
    kind: str
    segment_key: str
    balance: object | None
    position: object | None
    earn_holding: object | None
    valuation: object | None
    status: object | None
    observed_order: object | None


class AccountEvent(Protocol):
    metadata: AccountEventMetadata
    account_id: str
    change: AccountEventChange

    @property
    def stream_id(self) -> str: ...

    @property
    def sequence(self) -> int: ...

    @property
    def launch_id(self) -> str | None: ...

    @property
    def instance_id(self) -> str | None: ...


__all__ = ["AccountEvent", "AccountEventChange", "AccountEventMetadata"]
