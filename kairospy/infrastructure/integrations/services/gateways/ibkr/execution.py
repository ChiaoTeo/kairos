from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from kairospy.domain.order import OrderSide, OrderType
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.execution import (
    ConnectionOrderCancelRequest,
    ConnectionOrderCancelResult,
    ConnectionOrderOptions,
    ConnectionOrderSubmissionRequest,
    ConnectionOrderSubmissionResult,
)
from kairospy.infrastructure.integrations.domain import TransportKind
from kairospy.infrastructure.integrations.services.connections.connection import Connection


class IBKRGatewayDriver(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def reconnect(self) -> None: ...
    def submit(self, *, symbol: str, side: OrderSide, order_type: OrderType, quantity: Decimal, limit_price: Decimal | None, options: ConnectionOrderOptions | None) -> tuple[str, str]: ...
    def cancel(self, *, order_venue_id: str) -> str: ...


class IBKRExecutionConnection(Connection):
    """IB Gateway order adapter backed by the maintained ``ib_async`` client."""

    def __init__(
        self,
        spec: IntegrationConnectionSpec,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        client_id: int = 0,
        timeout_seconds: float = 10.0,
        gateway: IBKRGatewayDriver | None = None,
    ) -> None:
        if spec.transport is not TransportKind.REQUEST_API:
            raise ValueError("IBKR Gateway connection requires request_api transport")
        if timeout_seconds <= 0:
            raise ValueError("IBKR Gateway timeout must be positive")
        self.gateway = gateway or _IbAsyncGateway(
            host=host,
            port=port or (4002 if _mode_value(spec.mode) == "paper" else 4001),
            client_id=client_id,
            timeout_seconds=timeout_seconds,
        )
        super().__init__(spec, components=(self.gateway,))

    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult:
        venue_order_id, status = self.gateway.submit(
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            limit_price=request.limit_price,
            options=request.options,
        )
        if not str(venue_order_id).strip():
            raise RuntimeError("IBKR did not return an order id")
        return ConnectionOrderSubmissionResult(str(venue_order_id), str(status or "submitted"))

    def cancel(self, request: ConnectionOrderCancelRequest) -> ConnectionOrderCancelResult:
        status = self.gateway.cancel(order_venue_id=request.order_venue_id)
        return ConnectionOrderCancelResult(request.order_venue_id, str(status or "canceled"))


class IBKRExecutionGateway:
    def open(self, spec: IntegrationConnectionSpec) -> IBKRExecutionConnection:
        return IBKRExecutionConnection(spec)


class _IbAsyncGateway:
    def __init__(self, *, host: str, port: int, client_id: int, timeout_seconds: float) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout_seconds = timeout_seconds
        self._ib = None

    def start(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            return
        try:
            from ib_async import IB
        except ImportError as error:
            raise RuntimeError("IBKR support requires the ibkr extra: uv sync --extra ibkr") from error
        self._ib = IB()
        self._ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.timeout_seconds)

    def stop(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()
        self._ib = None

    def reconnect(self) -> None:
        self.stop()
        self.start()

    def submit(self, *, symbol: str, side: OrderSide, order_type: OrderType, quantity: Decimal, limit_price: Decimal | None, options: ConnectionOrderOptions | None) -> tuple[str, str]:
        self.start()
        from ib_async import LimitOrder, MarketOrder, Stock

        contract = self._qualify(Stock(symbol.strip().upper(), "SMART", "USD"))
        action = "BUY" if side is OrderSide.BUY else "SELL"
        if order_type is OrderType.MARKET:
            order = MarketOrder(action, float(quantity))
        else:
            if limit_price is None:
                raise ValueError("IBKR limit order requires a limit price")
            order = LimitOrder(action, float(quantity), float(limit_price))
        if options is not None and options.time_in_force:
            order.tif = options.time_in_force.upper()
        trade = self._ib.placeOrder(contract, order)
        order_id = getattr(getattr(trade, "order", None), "orderId", None)
        if order_id is None:
            raise RuntimeError("IBKR trade response did not contain an order id")
        status = getattr(getattr(trade, "orderStatus", None), "status", "submitted")
        return str(order_id), str(status)

    def cancel(self, *, order_venue_id: str) -> str:
        self.start()
        order_id = int(order_venue_id)
        for trade in self._ib.openTrades():
            order = getattr(trade, "order", None)
            if getattr(order, "orderId", None) == order_id:
                self._ib.cancelOrder(order)
                return str(getattr(getattr(trade, "orderStatus", None), "status", "canceled"))
        raise LookupError(f"IBKR open order was not found: {order_venue_id}")

    def _qualify(self, contract: object) -> object:
        contracts = self._ib.qualifyContracts(contract)
        if not contracts:
            raise LookupError(f"IBKR contract was not found: {getattr(contract, 'symbol', contract)}")
        return contracts[0]


__all__ = ["IBKRExecutionConnection", "IBKRGatewayDriver", "IBKRExecutionGateway"]


def _mode_value(value: object) -> str:
    return str(getattr(value, "value", value)).strip().lower()
