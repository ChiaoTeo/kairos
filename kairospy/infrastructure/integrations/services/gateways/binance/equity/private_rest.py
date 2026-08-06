from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from kairospy.domain.order import OrderSide, OrderType
from kairospy.domain.reference import AssetType
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.execution import (
    ConnectionOrderCancelRequest,
    ConnectionOrderCancelResult,
    ConnectionOrderSubmissionRequest,
    ConnectionOrderSubmissionResult,
)
from kairospy.infrastructure.integrations.domain import AccessScope, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.connections.connection import Connection

from .client import BinanceEquityRestClient
from .operations import BinanceEquityOrderOperations


class BinanceEquityExecutionConnection(Connection):
    """Binance Stocks Trading signed REST order-entry connection."""

    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self.order_operations = BinanceEquityOrderOperations(
            BinanceEquityRestClient(credential_id=spec.credential.id if spec.credential else None)
        )
        super().__init__(spec, components=())

    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult:
        if request.asset_type is not AssetType.EQUITY:
            raise ValueError("Binance Equity order connection requires an equity order")
        if request.order_type is not OrderType.LIMIT:
            raise ValueError("Binance Equity connection currently requires LIMIT orders")
        options = request.options
        params: dict[str, Any] = {
            "symbol": request.symbol,
            "side": request.side.value.upper(),
            "orderType": request.order_type.value.upper(),
            "quoteAsset": "USDC",
            "price": str(request.limit_price),
            "quantity": str(request.quantity),
            "timeInForce": "DAY",
            "tradingSession": (options.trading_session if options and options.trading_session else "RTH"),
            "walletType": "MAIN",
            "tokenize": True,
        }
        if options is not None:
            if options.time_in_force is not None:
                params["timeInForce"] = options.time_in_force.upper()
            if options.quote_asset is not None:
                params["quoteAsset"] = options.quote_asset.upper()
            if options.wallet_type is not None:
                params["walletType"] = options.wallet_type.upper()
            if options.tokenize is not None:
                params["tokenize"] = options.tokenize
        if request.client_order_id:
            params["clientOrderId"] = _client_order_id(request.client_order_id)
        payload = self.order_operations.submit(params)
        if not isinstance(payload, Mapping):
            raise ValueError("Binance Equity order response must be an object")
        order_id = str(payload.get("orderId") or "")
        return ConnectionOrderSubmissionResult(
            order_venue_id=order_id,
            status=str(payload.get("status") or ""),
            reason=str(payload.get("msg") or payload.get("message") or ""),
        )

    def cancel(self, request: ConnectionOrderCancelRequest) -> ConnectionOrderCancelResult:
        payload = self.order_operations.cancel({"orderId": request.order_venue_id})
        if not isinstance(payload, Mapping):
            raise ValueError("Binance Equity cancel response must be an object")
        return ConnectionOrderCancelResult(
            order_venue_id=str(payload.get("orderId") or request.order_venue_id),
            status=str(payload.get("status") or ""),
        )


class BinanceEquityExecutionGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceEquityExecutionConnection:
        _validate_private_equity(spec)
        return BinanceEquityExecutionConnection(spec)


def _validate_private_equity(spec: IntegrationConnectionSpec) -> None:
    if (
        spec.product is not ProductFamily.SPOT
        or spec.asset_type is not AssetType.EQUITY
        or spec.access is not AccessScope.PRIVATE
        or spec.transport is not TransportKind.REST
    ):
        raise ValueError("Binance Equity execution gateway received an incompatible connection spec")


def _client_order_id(value: str) -> str:
    normalized = "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
    if 32 <= len(normalized) <= 36:
        return normalized
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


__all__ = ["BinanceEquityExecutionConnection", "BinanceEquityExecutionGateway"]
