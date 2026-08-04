from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kairospy.domain.order import OrderSide, OrderType
from kairospy.infrastructure.integrations.application.account import ConnectionAccountReadData, ConnectionAccountReadRequest
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.execution import (
    ConnectionOrderCancelRequest,
    ConnectionOrderCancelResult,
    ConnectionOrderSubmissionRequest,
    ConnectionOrderSubmissionResult,
)
from kairospy.infrastructure.integrations.domain import AccessScope, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from .client import BinanceSpotRestClient
from .endpoints import BinanceSpotEndpoint, BinanceSpotEndpointKind
from .normalizers import BinanceSpotNormalizers
from .operations import BinanceSpotAccountOperations, BinanceSpotOrderOperations


class BinanceSpotAccountConnection(Connection):
    """Binance Spot account-read connection.

    Order entry is intentionally not part of this connection's surface.  A
    separate execution connection is selected for submit/cancel operations.
    """

    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self.account_operations = BinanceSpotAccountOperations(_private_client(spec))
        self.normalizers = BinanceSpotNormalizers()
        super().__init__(spec, components=())

    def read_account(self, request: ConnectionAccountReadRequest) -> ConnectionAccountReadData:
        balance = self.account_operations.account_snapshot()
        open_orders = self.account_operations.open_orders(symbol=request.symbol) if request.fetch_orders else ()
        snapshot = self.normalizers.account_snapshot(
            balance,
            context=request.context,
            observed_at=request.observed_at,
            open_orders=open_orders,
        )
        return ConnectionAccountReadData(snapshot=snapshot)


class BinanceSpotAccountGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceSpotAccountConnection:
        _validate_private_rest(spec)
        return BinanceSpotAccountConnection(spec)


class BinanceSpotExecutionConnection(Connection):
    """Binance Spot order-entry connection."""

    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self.order_operations = BinanceSpotOrderOperations(_private_client(spec))
        super().__init__(spec, components=())

    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult:
        params: dict[str, Any] = {
            "symbol": request.symbol,
            "side": request.side.value.upper(),
            "type": request.order_type.value.upper(),
            "quantity": str(request.quantity),
        }
        if request.client_order_id:
            params["newClientOrderId"] = request.client_order_id
        if request.limit_price is not None:
            params["price"] = str(request.limit_price)
            params["timeInForce"] = "GTC"
        if request.options is not None:
            for key, value in (
                ("timeInForce", request.options.time_in_force),
                ("reduceOnly", request.options.reduce_only),
                ("postOnly", request.options.post_only),
            ):
                if value is not None:
                    params[key] = value
        payload = self.order_operations.submit(params)
        if not isinstance(payload, Mapping):
            raise ValueError("Binance order response must be an object")
        return ConnectionOrderSubmissionResult(str(payload.get("orderId") or ""), str(payload.get("status") or ""))

    def cancel(self, request: ConnectionOrderCancelRequest) -> ConnectionOrderCancelResult:
        params: dict[str, Any] = {"symbol": request.symbol, "orderId": request.order_venue_id}
        if request.options is not None and request.options.reduce_only is not None:
            params["reduceOnly"] = request.options.reduce_only
        payload = self.order_operations.cancel(params)
        if not isinstance(payload, Mapping):
            raise ValueError("Binance cancel response must be an object")
        return ConnectionOrderCancelResult(
            str(payload.get("orderId") or request.order_venue_id),
            str(payload.get("status") or ""),
        )


class BinanceSpotExecutionGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceSpotExecutionConnection:
        _validate_private_rest(spec)
        return BinanceSpotExecutionConnection(spec)


def _private_client(spec: IntegrationConnectionSpec) -> BinanceSpotRestClient:
    return BinanceSpotRestClient(
        credential_id=spec.credential.id if spec.credential else None,
        endpoint=BinanceSpotEndpoint(
            BinanceSpotEndpointKind.PRIVATE_REST,
            "https://api.binance.com",
        ),
    )


def _validate_private_rest(spec: IntegrationConnectionSpec) -> None:
    if (
        spec.product is not ProductFamily.SPOT
        or spec.access is not AccessScope.PRIVATE
        or spec.transport is not TransportKind.REST
    ):
        raise ValueError("Binance Spot private REST gateway received an incompatible connection spec")


__all__ = [
    "BinanceSpotAccountConnection",
    "BinanceSpotAccountGateway",
    "BinanceSpotExecutionConnection",
    "BinanceSpotExecutionGateway",
]
