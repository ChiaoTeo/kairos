"""Read-only Strategy view of the owner-native Risk event contract."""

from __future__ import annotations

from typing import Protocol


class RiskEventMetadata(Protocol):
    stream_id: str
    sequence: int
    producer: str
    occurred_at_unix_nanos: int


class RiskEvent(Protocol):
    metadata: RiskEventMetadata
    kind: str
    account_id: str | None
    strategy_id: str | None
    payload: object
    data: object

    @property
    def stream_id(self) -> str: ...

    @property
    def sequence(self) -> int: ...

    @property
    def launch_id(self) -> str | None: ...

    @property
    def instance_id(self) -> str | None: ...


__all__ = ["RiskEvent", "RiskEventMetadata"]
