from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from kairospy.application.usecases.execution.live import (
    OrderCancelRequest,
    OrderCancelResult,
    OrderExecutionPort,
    OrderSubmissionRequest,
    OrderSubmissionResult,
)
from kairospy.core.account import AccountBookRef


class BrokerResolver(Protocol):
    def __call__(self, account: AccountBookRef) -> object | None:
        ...


class BrokerOrderExecutionAdapter(OrderExecutionPort):
    def __init__(
        self,
        broker: object | None = None,
        *,
        broker_resolver: BrokerResolver | None = None,
    ) -> None:
        self.broker = broker
        self.broker_resolver = broker_resolver

    def submit(self, request: OrderSubmissionRequest) -> OrderSubmissionResult:
        broker = self._broker_for(request.account)
        response = broker.create_order(
            request.symbol,
            side=request.side.value,
            type=request.order_type.value,
            amount=request.quantity,
            price=request.limit_price,
            params=request.integration_options,
        )
        order_venue_id = _response_text(response, "id") or _response_text(response, "orderId")
        return OrderSubmissionResult(order_venue_id=order_venue_id, status=_response_text(response, "status"))

    def cancel(self, request: OrderCancelRequest) -> OrderCancelResult:
        broker = self._broker_for(request.account)
        response = broker.cancel_order(
            request.order_venue_id,
            symbol=request.symbol,
            params=request.integration_options,
        )
        return OrderCancelResult(
            order_venue_id=_response_text(response, "id") or request.order_venue_id,
            status=_response_text(response, "status"),
        )

    def _broker_for(self, account: AccountBookRef) -> object:
        broker = self.broker
        if self.broker_resolver is not None:
            broker = self.broker_resolver(account) or broker
        if broker is None:
            raise RuntimeError(f"no order execution broker configured for account: {account.value}")
        return broker


def _response_text(response: Mapping[str, object], key: str) -> str:
    return str(response.get(key) or "").strip()


__all__ = ["BrokerOrderExecutionAdapter"]
