"""Ports consumed by the execution usecase."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Mapping, Protocol

from kairospy.domain.account import AccountRuntimeContext
from kairospy.domain.execution import ExecutionUpdate


class OrderAuditStore(Protocol):
    """Durable execution audit port consumed by the execution usecase.

    The execution module owns the meaning of these records. Concrete stores
    (SQLite, test memory stores, or a future remote store) are selected by
    composition.
    """

    def record_receipt(self, record: Mapping[str, object]) -> None: ...

    def record_transition(self, record: Mapping[str, object]) -> None: ...

    def events(
        self,
        *,
        order_id: str | None = None,
        venue_order_id: str | None = None,
        instance_id: str | None = None,
        account: str | None = None,
        broker: str | None = None,
        exchange: str | None = None,
        product_type: str | None = None,
        symbol: str | None = None,
        status: str | None = None,
        event_kind: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> tuple[Mapping[str, object], ...]: ...

    def trace(self, order_id: str, *, instance_id: str | None = None) -> tuple[Mapping[str, object], ...]: ...


class ExecutionUpdateSource(Protocol):
    def events(
        self,
        account: AccountRuntimeContext,
        *,
        symbol: str | None = None,
    ) -> AsyncIterator[ExecutionUpdate]: ...


__all__ = [
    "OrderAuditStore",
    "ExecutionUpdateSource",
]
