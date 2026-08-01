from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable

from kairospy.application.usecases.execution.live import (
    OrderCancelRequest,
    OrderCancelResult,
    OrderSubmissionRequest,
    OrderSubmissionResult,
)
from kairospy.core.account import AccountBookRef
from kairospy.infrastructure.integrations.payloads.types import IntegrationParams, OrderSubmissionResponse, RawPayloadRows
from kairospy.infrastructure.integrations.services.resolver import DEFAULT_INTEGRATION_RESOLVER, IntegrationResolver


@dataclass(frozen=True, slots=True)
class OrderIntegrationApplicationService:
    """Concrete order integration service exposed to application composition."""

    book: AccountBookRef
    credential: str | None = None
    resolver: IntegrationResolver = DEFAULT_INTEGRATION_RESOLVER
    mode_label: str = "runtime"
    error_type: type[Exception] = ValueError
    client: object | None = None
    client_resolver: Callable[[AccountBookRef], object | None] | None = None

    def submit(self, request: OrderSubmissionRequest) -> OrderSubmissionResult:
        response = self._execution_client_for(request.account).create_order(
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
        response = self._execution_client_for(request.account).cancel_order(
            request.order_venue_id,
            symbol=request.symbol,
            params=request.integration_options,
        )
        return OrderCancelResult(
            order_venue_id=_response_text(response, "id") or request.order_venue_id,
            status=_response_text(response, "status"),
        )

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        return self._query_client().fetch_open_orders(symbol, since=since, limit=limit, params=params)

    def fetch_closed_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        return self._query_client().fetch_closed_orders(symbol, since=since, limit=limit, params=params)

    def create_order(
        self,
        symbol: str,
        *,
        side: str,
        type: str,
        amount: object,
        price: object | None = None,
        params: IntegrationParams | None = None,
    ) -> OrderSubmissionResponse:
        return self._execution_client().create_order(symbol, side=side, type=type, amount=amount, price=price, params=params)

    def cancel_order(
        self,
        id: str,
        *,
        symbol: str | None = None,
        params: IntegrationParams | None = None,
    ) -> OrderSubmissionResponse:
        return self._execution_client().cancel_order(id, symbol=symbol, params=params)

    def _query_client(self) -> object:
        client = self._configured_client(self.book)
        if client is not None:
            return client
        return self.resolver.order_query_for_book(
            self.book,
            self.credential,
            mode_label=self.mode_label,
            error_type=self.error_type,
        )

    def _execution_client(self) -> object:
        return self._execution_client_for(self.book)

    def _execution_client_for(self, book: AccountBookRef) -> object:
        client = self._configured_client(book)
        if client is not None:
            return client
        return self.resolver.order_execution_for_book(
            book,
            self.credential,
            mode_label=self.mode_label,
            error_type=self.error_type,
        )

    def _configured_client(self, book: AccountBookRef) -> object | None:
        if self.client_resolver is not None:
            return self.client_resolver(book) or self.client
        return self.client


def _response_text(response: Mapping[str, object], key: str) -> str:
    return str(response.get(key) or "").strip()


__all__ = ["OrderIntegrationApplicationService"]
