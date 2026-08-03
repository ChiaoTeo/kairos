from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kairospy.infrastructure.integrations.application.account import ConnectionAccountBootstrapData, ConnectionAccountBootstrapRequest
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.execution import (
    ConnectionOrderCancelRequest,
    ConnectionOrderCancelResult,
    ConnectionOrderSubmissionRequest,
    ConnectionOrderSubmissionResult,
)
from kairospy.infrastructure.integrations.services.clients.binance_spot import BinanceSpotRestClient
from kairospy.infrastructure.integrations.services.connections.base import ConnectionService
from kairospy.infrastructure.integrations.services.endpoints.binance_spot import BinanceSpotEndpoint, BinanceSpotEndpointKind
from kairospy.infrastructure.integrations.services.operations.binance_spot import BinanceSpotAccountOperations, BinanceSpotOrderOperations
from kairospy.infrastructure.integrations.services.translators.binance_spot import BinanceSpotPayloadTranslator
from kairospy.domain.order import OrderSide, OrderType


class BinanceSpotPrivateRestConnection(ConnectionService):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        client = BinanceSpotRestClient(
            credential_id=spec.credential.id if spec.credential else None,
            endpoint=BinanceSpotEndpoint(BinanceSpotEndpointKind.PRIVATE_ACCOUNT_REST, "https://api.binance.com"),
        )
        self.account_operations = BinanceSpotAccountOperations(client)
        self.order_operations = BinanceSpotOrderOperations(client)
        self.translator = BinanceSpotPayloadTranslator()
        super().__init__(spec, components=())

    def bootstrap(self, request: ConnectionAccountBootstrapRequest) -> ConnectionAccountBootstrapData:
        balance = self.account_operations.account_snapshot()
        open_orders = self.account_operations.open_orders(symbol=request.symbol) if request.fetch_orders else ()
        snapshot = self.translator.account_snapshot(balance, context=request.context, observed_at=request.observed_at, open_orders=open_orders)
        return ConnectionAccountBootstrapData(snapshot=snapshot)

    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult:
        params: dict[str, Any] = {"symbol": request.symbol, "side": request.side.value.upper(), "type": request.order_type.value.upper(), "quantity": str(request.quantity)}
        if request.limit_price is not None:
            params["price"] = str(request.limit_price)
            params["timeInForce"] = "GTC"
        if request.options is not None:
            for key, value in (("timeInForce", request.options.time_in_force), ("reduceOnly", request.options.reduce_only), ("postOnly", request.options.post_only)):
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
        return ConnectionOrderCancelResult(str(payload.get("orderId") or request.order_venue_id), str(payload.get("status") or ""))


__all__ = ["BinanceSpotPrivateRestConnection"]
