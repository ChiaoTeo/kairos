"""Business-shaped queries for persisted order audit records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kairospy.application.usecases.execution.protocol import OrderAuditStore


@dataclass(frozen=True, slots=True)
class OrderAuditQueries:
    store: OrderAuditStore

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
    ) -> tuple[Mapping[str, object], ...]:
        return self.store.events(
            order_id=order_id,
            venue_order_id=venue_order_id,
            instance_id=instance_id,
            account=account,
            broker=broker,
            exchange=exchange,
            product_type=product_type,
            symbol=symbol,
            status=status,
            event_kind=event_kind,
            since=since,
            until=until,
            limit=limit,
        )

    def trace(self, order_id: str, *, instance_id: str | None = None) -> tuple[Mapping[str, object], ...]:
        return self.store.trace(order_id, instance_id=instance_id)


__all__ = ["OrderAuditQueries"]
