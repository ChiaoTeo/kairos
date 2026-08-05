from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kairospy.domain.account import AccountBalance, AccountRuntimeContext, AccountSnapshot, AccountSource, OpenOrderSnapshot, PositionSnapshot
from kairospy.domain.reference import MarketRef
from kairospy.domain.order import OrderSide
from kairospy.infrastructure.integrations.application.account import ConnectionAccountReadData, ConnectionAccountReadRequest
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.execution import ConnectionOrderCancelRequest, ConnectionOrderCancelResult, ConnectionOrderSubmissionRequest, ConnectionOrderSubmissionResult
from kairospy.infrastructure.integrations.domain import AccessScope, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.connections.connection import Connection

from .client import BinanceOptionsRestClient
from .operations import BinanceOptionsAccountOperations, BinanceOptionsOrderOperations


class BinanceOptionsAccountConnection(Connection):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self.operations = BinanceOptionsAccountOperations(_client(spec))
        super().__init__(spec, components=())

    def read_account(self, request: ConnectionAccountReadRequest) -> ConnectionAccountReadData:
        payload = self.operations.account_snapshot()
        orders = self.operations.open_orders(symbol=request.symbol) if request.fetch_orders else ()
        return ConnectionAccountReadData(_snapshot(payload, orders, request.context, request.observed_at))


class BinanceOptionsExecutionConnection(Connection):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self.operations = BinanceOptionsOrderOperations(_client(spec))
        super().__init__(spec, components=())

    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult:
        params: dict[str, Any] = {"symbol": request.symbol.upper(), "side": request.side.value.upper(), "type": request.order_type.value.upper(), "quantity": str(request.quantity)}
        if request.limit_price is not None:
            params["price"] = str(request.limit_price)
            params["timeInForce"] = "GTC"
        if request.client_order_id:
            params["clientOrderId"] = request.client_order_id
        payload = self.operations.submit(params)
        if not isinstance(payload, Mapping):
            raise ValueError("Binance Options order response must be an object")
        return ConnectionOrderSubmissionResult(str(payload.get("orderId") or ""), str(payload.get("status") or ""))

    def cancel(self, request: ConnectionOrderCancelRequest) -> ConnectionOrderCancelResult:
        payload = self.operations.cancel({"symbol": (request.symbol or "").upper(), "orderId": request.order_venue_id})
        if not isinstance(payload, Mapping):
            raise ValueError("Binance Options cancel response must be an object")
        return ConnectionOrderCancelResult(str(payload.get("orderId") or request.order_venue_id), str(payload.get("status") or ""))


def _client(spec: IntegrationConnectionSpec) -> BinanceOptionsRestClient:
    if spec.product is not ProductFamily.OPTIONS or spec.access is not AccessScope.PRIVATE or spec.transport is not TransportKind.REST:
        raise ValueError("Binance Options private REST gateway received an incompatible connection spec")
    return BinanceOptionsRestClient(credential_id=spec.credential.id if spec.credential else None)


def _snapshot(payload: object, orders: object, context: AccountRuntimeContext, observed_at: object) -> AccountSnapshot:
    values = payload if isinstance(payload, Mapping) else {}
    balances = tuple(AccountBalance.from_free_locked(str(row.get("asset")), _decimal(row.get("available")), _decimal(row.get("freeze")), source=AccountSource.VENUE) for row in values.get("assets", ()) if isinstance(row, Mapping) and str(row.get("asset") or "").strip())
    positions = tuple(PositionSnapshot(MarketRef.ephemeral(venue="binance", market="options", source_symbol=str(row.get("symbol"))).instrument_id, _decimal(row.get("quantity")), AccountSource.VENUE, average_price=_decimal(row.get("averagePrice"))) for row in values.get("positions", ()) if isinstance(row, Mapping) and _decimal(row.get("quantity")) != 0)
    open_orders = tuple(OpenOrderSnapshot(str(row.get("orderId") or "unknown"), MarketRef.ephemeral(venue="binance", market="options", source_symbol=str(row.get("symbol") or "UNKNOWN")).instrument_id, str(row.get("side") or "unknown"), _decimal(row.get("quantity")), AccountSource.VENUE) for row in orders if isinstance(row, Mapping) and _decimal(row.get("quantity")) > 0) if isinstance(orders, list) else ()
    return AccountSnapshot(context, balances=balances, positions=positions, open_orders=open_orders, observed_at=observed_at, source=AccountSource.VENUE)


def _decimal(value: object):
    from decimal import Decimal
    try:
        return Decimal(str(value or "0"))
    except (TypeError, ValueError):
        return Decimal("0")


__all__ = ["BinanceOptionsAccountConnection", "BinanceOptionsExecutionConnection"]
