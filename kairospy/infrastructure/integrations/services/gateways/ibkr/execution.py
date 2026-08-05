"""IBKR broker connections.

The optional ``ib_async`` dependency is deliberately contained in this
module.  Connections expose only the integration application's canonical
request/result types; tests can inject ``IBKRGatewayDriver`` without opening
TWS or IB Gateway.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from kairospy.domain.account import (
    AccountBalance,
    AccountContext,
    AccountSnapshot,
    AccountSource,
    MarginState,
    OpenOrderSnapshot,
    PositionSnapshot,
)
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.order import OrderEventKind, OrderSide, OrderType
from kairospy.domain.reference import MarketRef
from kairospy.infrastructure.integrations.application.account import (
    ConnectionAccountReadData,
    ConnectionAccountReadRequest,
    ConnectionAccountStreamRequest,
    AccountStreamConnection,
    OrderUpdateConnection,
)
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.execution import (
    ConnectionOrderCancelRequest,
    ConnectionOrderCancelResult,
    ConnectionOrderOptions,
    ConnectionOrderSubmissionRequest,
    ConnectionOrderSubmissionResult,
)
from kairospy.infrastructure.integrations.domain import IntegrationCapability, TransportKind
from kairospy.infrastructure.integrations.services.connections.connection import Connection


@dataclass(frozen=True, slots=True)
class IBKRAccountValue:
    tag: str
    value: Decimal
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class IBKRPosition:
    symbol: str
    quantity: Decimal
    average_price: Decimal | None = None
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class IBKROpenOrder:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    filled_quantity: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class IBKRExecutionEvent:
    order_id: str
    symbol: str
    status: str
    side: OrderSide | None = None
    order_type: OrderType | None = None
    quantity: Decimal | None = None
    limit_price: Decimal | None = None
    filled_quantity: Decimal | None = None
    remaining_quantity: Decimal | None = None
    fill_quantity: Decimal | None = None
    fill_price: Decimal | None = None
    observed_at: datetime | None = None
    reason: str = ""
    execution_id: str = ""
    fee_currency: str | None = None
    fee_amount: Decimal = Decimal("0")


class IBKRGatewayDriver(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def reconnect(self) -> None: ...
    def submit(self, *, symbol: str, side: OrderSide, order_type: OrderType, quantity: Decimal, limit_price: Decimal | None, options: ConnectionOrderOptions | None) -> tuple[str, str]: ...
    def cancel(self, *, order_venue_id: str) -> str: ...
    def account_values(self, *, account_id: str) -> tuple[IBKRAccountValue, ...]: ...
    def positions(self, *, account_id: str) -> tuple[IBKRPosition, ...]: ...
    def open_orders(self, *, account_id: str) -> tuple[IBKROpenOrder, ...]: ...
    def execution_events(self, *, account_id: str, symbol: str | None = None) -> AsyncIterator[IBKRExecutionEvent]: ...


class _AsyncDriverResource:
    """Adapt ib_async's synchronous lifecycle to the connection contract."""

    def __init__(self, driver: IBKRGatewayDriver) -> None:
        self.driver = driver

    async def start(self) -> None:
        self.driver.start()

    async def stop(self) -> None:
        self.driver.stop()

    async def reconnect(self) -> None:
        self.driver.reconnect()


class IBKRExecutionConnection(Connection):
    def __init__(self, spec: IntegrationConnectionSpec, *, gateway: IBKRGatewayDriver | None = None, **options: object) -> None:
        _validate(spec, IntegrationCapability.ORDER_ENTRY)
        self.gateway = gateway or _IbAsyncGateway.from_options(spec, options)
        super().__init__(spec, components=(_AsyncDriverResource(self.gateway),))

    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult:
        venue_order_id, status = self.gateway.submit(symbol=request.symbol, side=request.side, order_type=request.order_type, quantity=request.quantity, limit_price=request.limit_price, options=request.options)
        if not str(venue_order_id).strip():
            raise RuntimeError("IBKR did not return an order id")
        return ConnectionOrderSubmissionResult(str(venue_order_id), str(status or "submitted"))

    def cancel(self, request: ConnectionOrderCancelRequest) -> ConnectionOrderCancelResult:
        status = self.gateway.cancel(order_venue_id=request.order_venue_id)
        return ConnectionOrderCancelResult(request.order_venue_id, str(status or "canceled"))


class IBKRAccountConnection(Connection):
    def __init__(self, spec: IntegrationConnectionSpec, *, gateway: IBKRGatewayDriver | None = None, **options: object) -> None:
        _validate(spec, IntegrationCapability.ACCOUNT_READ)
        self.gateway = gateway or _IbAsyncGateway.from_options(spec, options)
        super().__init__(spec, components=(_AsyncDriverResource(self.gateway),))

    def read_account(self, request: ConnectionAccountReadRequest) -> ConnectionAccountReadData:
        account_id = str(request.context.book.account_id)
        values = self.gateway.account_values(account_id=account_id)
        positions = self.gateway.positions(account_id=account_id)
        orders = self.gateway.open_orders(account_id=account_id) if request.fetch_orders else ()
        return ConnectionAccountReadData(_account_snapshot(request, values, positions, orders))


class IBKRExecutionStreamConnection(Connection, OrderUpdateConnection):
    def __init__(self, spec: IntegrationConnectionSpec, *, gateway: IBKRGatewayDriver | None = None, **options: object) -> None:
        _validate(spec, IntegrationCapability.EXECUTION_STREAM)
        self.gateway = gateway or _IbAsyncGateway.from_options(spec, options)
        super().__init__(spec, components=(_AsyncDriverResource(self.gateway),))

    async def execution_updates(self, request: ConnectionAccountStreamRequest, *, trades_only: bool = False) -> AsyncIterator[ExecutionUpdate]:
        async for event in self.gateway.execution_events(account_id=str(request.context.book.account_id), symbol=request.symbol):
            if trades_only and event.fill_quantity is None:
                continue
            yield _execution_update(event, request.context)


class IBKRAccountStreamConnection(Connection, AccountStreamConnection):
    def __init__(self, spec: IntegrationConnectionSpec, *, gateway: IBKRGatewayDriver | None = None, poll_seconds: float = 5.0, **options: object) -> None:
        _validate(spec, IntegrationCapability.ACCOUNT_STREAM)
        if poll_seconds <= 0:
            raise ValueError("IBKR account stream poll interval must be positive")
        self.gateway = gateway or _IbAsyncGateway.from_options(spec, options)
        self.poll_seconds = poll_seconds
        super().__init__(spec, components=(_AsyncDriverResource(self.gateway),))

    async def account_snapshots(self, request: ConnectionAccountStreamRequest) -> AsyncIterator[AccountSnapshot]:
        while True:
            now = datetime.now(timezone.utc)
            account_id = str(request.context.book.account_id)
            values = self.gateway.account_values(account_id=account_id)
            positions = self.gateway.positions(account_id=account_id)
            orders = self.gateway.open_orders(account_id=account_id)
            read_request = ConnectionAccountReadRequest(request.context, now, request.symbol, True)
            yield _account_snapshot(read_request, values, positions, orders)
            await asyncio.sleep(self.poll_seconds)


class IBKRExecutionGateway:
    def __init__(self, *, gateway: IBKRGatewayDriver | None = None, **options: object) -> None:
        self.gateway, self.options = gateway, options

    def open(self, spec: IntegrationConnectionSpec) -> IBKRExecutionConnection:
        return IBKRExecutionConnection(spec, gateway=self.gateway, **self.options)


class IBKRAccountGateway(IBKRExecutionGateway):
    def open(self, spec: IntegrationConnectionSpec) -> IBKRAccountConnection:
        return IBKRAccountConnection(spec, gateway=self.gateway, **self.options)


class IBKRExecutionStreamGateway(IBKRExecutionGateway):
    def open(self, spec: IntegrationConnectionSpec) -> IBKRExecutionStreamConnection:
        return IBKRExecutionStreamConnection(spec, gateway=self.gateway, **self.options)


class IBKRAccountStreamGateway(IBKRExecutionGateway):
    def open(self, spec: IntegrationConnectionSpec) -> IBKRAccountStreamConnection:
        return IBKRAccountStreamConnection(spec, gateway=self.gateway, **self.options)


class _IbAsyncGateway:
    def __init__(self, *, host: str, port: int, client_id: int, timeout_seconds: float) -> None:
        self.host, self.port, self.client_id, self.timeout_seconds = host, port, client_id, timeout_seconds
        self._ib = None

    @classmethod
    def from_options(cls, spec: IntegrationConnectionSpec, options: dict[str, object]) -> "_IbAsyncGateway":
        mode = _mode_value(spec.mode)
        return cls(host=str(options.get("host", "127.0.0.1")), port=int(options.get("port", 4002 if mode == "paper" else 4001)), client_id=int(options.get("client_id", 0)), timeout_seconds=float(options.get("timeout_seconds", 10.0)))

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
        return str(order_id), str(getattr(getattr(trade, "orderStatus", None), "status", "submitted"))

    def cancel(self, *, order_venue_id: str) -> str:
        self.start()
        order_id = int(order_venue_id)
        for trade in self._ib.openTrades():
            order = getattr(trade, "order", None)
            if getattr(order, "orderId", None) == order_id:
                self._ib.cancelOrder(order)
                return str(getattr(getattr(trade, "orderStatus", None), "status", "canceled"))
        raise LookupError(f"IBKR open order was not found: {order_venue_id}")

    def account_values(self, *, account_id: str) -> tuple[IBKRAccountValue, ...]:
        self.start()
        return tuple(IBKRAccountValue(str(item.tag), _decimal(item.value), str(item.currency or "USD")) for item in self._ib.accountSummary(account_id))

    def positions(self, *, account_id: str) -> tuple[IBKRPosition, ...]:
        self.start()
        return tuple(IBKRPosition(str(item.contract.symbol), _decimal(item.position), _decimal(item.avgCost), str(item.contract.currency or "USD")) for item in self._ib.positions(account_id) if _decimal(item.position) != 0)

    def open_orders(self, *, account_id: str) -> tuple[IBKROpenOrder, ...]:
        self.start()
        result = []
        for trade in self._ib.openTrades():
            order, status, contract = trade.order, trade.orderStatus, trade.contract
            if getattr(status, "account", account_id) not in {account_id, ""}:
                continue
            total = _decimal(getattr(order, "totalQuantity", 0))
            filled = _decimal(getattr(status, "filled", 0))
            if total > filled:
                result.append(IBKROpenOrder(str(order.orderId), str(contract.symbol), _side(order.action), total, filled))
        return tuple(result)

    async def execution_events(self, *, account_id: str, symbol: str | None = None) -> AsyncIterator[IBKRExecutionEvent]:
        self.start()
        queue: asyncio.Queue[IBKRExecutionEvent] = asyncio.Queue()

        def on_status(trade) -> None:
            status_account = str(getattr(trade.orderStatus, "account", "") or "")
            if status_account not in {"", account_id}:
                return
            if symbol is None or str(getattr(trade.contract, "symbol", "")) == symbol:
                queue.put_nowait(_trade_event(trade))

        def on_execution(trade, fill) -> None:
            status_account = str(getattr(trade.orderStatus, "account", "") or "")
            if status_account not in {"", account_id}:
                return
            if symbol is None or str(getattr(trade.contract, "symbol", "")) == symbol:
                queue.put_nowait(_fill_event(trade, fill))

        self._ib.orderStatusEvent += on_status
        self._ib.execDetailsEvent += on_execution
        try:
            while self._ib.isConnected():
                yield await queue.get()
        finally:
            self._ib.orderStatusEvent -= on_status
            self._ib.execDetailsEvent -= on_execution

    def _qualify(self, contract: object) -> object:
        contracts = self._ib.qualifyContracts(contract)
        if not contracts:
            raise LookupError(f"IBKR contract was not found: {getattr(contract, 'symbol', contract)}")
        return contracts[0]


def _account_snapshot(request, values, positions, orders) -> AccountSnapshot:
    by_tag = {item.tag: item for item in values}
    total = by_tag.get("TotalCashValue")
    available = by_tag.get("AvailableFunds")
    total_cash = total.value if total else Decimal("0")
    free_cash = available.value if available else total_cash
    currency = (total or available or IBKRAccountValue("", Decimal("0"))).currency
    balances = (AccountBalance.from_free_locked(currency, free_cash, max(total_cash - free_cash, Decimal("0")), source=AccountSource.VENUE),) if total_cash or free_cash else ()
    margin = by_tag.get("MaintMarginReq")
    margins = () if margin is None else (MarginState(currency, max(margin.value, Decimal("0")), max(margin.value, Decimal("0")), AccountSource.VENUE, available=max(free_cash, Decimal("0"))),)
    venue = str(request.context.book.broker)
    return AccountSnapshot(request.context, balances=balances, margins=margins, positions=tuple(PositionSnapshot(_instrument(venue, item.symbol), item.quantity, AccountSource.VENUE, average_price=item.average_price, margin_currency=item.currency) for item in positions), open_orders=tuple(OpenOrderSnapshot(item.order_id, _instrument(venue, item.symbol), str(item.side), item.quantity - item.filled_quantity, AccountSource.VENUE) for item in orders if item.quantity > item.filled_quantity), observed_at=request.observed_at, source=AccountSource.VENUE)


def _execution_update(event: IBKRExecutionEvent, context: AccountContext) -> ExecutionUpdate:
    filled = event.filled_quantity or Decimal("0")
    kind = _event_kind(event.status, filled, event.remaining_quantity)
    return ExecutionUpdate(observed_at=event.observed_at or datetime.now(timezone.utc), kind=kind, order_venue_id=event.order_id, context=context, instrument_id=_instrument(str(context.book.broker), event.symbol), side=event.side, quantity=event.quantity, order_type=event.order_type, limit_price=event.limit_price, filled_quantity=event.filled_quantity, remaining_quantity=event.remaining_quantity, fill_quantity=event.fill_quantity, fill_price=event.fill_price, fee_currency=event.fee_currency, fee_amount=event.fee_amount, reason=event.reason, source="ibkr", metadata={"execution_id": event.execution_id} if event.execution_id else {})


def _trade_event(trade) -> IBKRExecutionEvent:
    order, status, contract = trade.order, trade.orderStatus, trade.contract
    order_type = OrderType.LIMIT if str(getattr(order, "orderType", "")).upper() == "LMT" else OrderType.MARKET
    return IBKRExecutionEvent(str(order.orderId), str(contract.symbol), str(status.status), _side(order.action), order_type, _decimal(order.totalQuantity), _decimal(getattr(order, "lmtPrice", 0)) or None, _decimal(status.filled), _decimal(status.remaining), observed_at=datetime.now(timezone.utc), reason=str(getattr(trade, "advancedError", "") or ""))


def _fill_event(trade, fill) -> IBKRExecutionEvent:
    event = _trade_event(trade)
    execution = fill.execution
    commission = getattr(fill, "commissionReport", None)
    return IBKRExecutionEvent(
        event.order_id,
        event.symbol,
        event.status,
        event.side,
        event.order_type,
        event.quantity,
        event.limit_price,
        event.filled_quantity,
        event.remaining_quantity,
        fill_quantity=_decimal(getattr(execution, "shares", 0)),
        fill_price=_decimal(getattr(execution, "price", 0)),
        observed_at=getattr(execution, "time", None) or datetime.now(timezone.utc),
        reason=event.reason,
        execution_id=str(getattr(execution, "execId", "") or ""),
        fee_currency=str(getattr(commission, "currency", "") or "") or None,
        fee_amount=max(_decimal(getattr(commission, "commission", 0)), Decimal("0")),
    )


def _event_kind(status: str, filled: Decimal, remaining: Decimal | None) -> OrderEventKind:
    value = status.strip().lower()
    if value == "filled" or (filled > 0 and remaining == 0): return OrderEventKind.FILLED
    if value in {"cancelled", "canceled"}: return OrderEventKind.CANCELED
    if value in {"inactive", "rejected"}: return OrderEventKind.REJECTED
    if filled > 0: return OrderEventKind.PARTIALLY_FILLED
    if value in {"presubmitted", "submitted"}: return OrderEventKind.ACKNOWLEDGED
    return OrderEventKind.UNKNOWN


def _instrument(venue: str, symbol: str):
    return MarketRef.ephemeral(venue=venue, market="equity", source_symbol=symbol).instrument_id


def _side(value: object) -> OrderSide:
    return OrderSide.BUY if str(value).upper() == "BUY" else OrderSide.SELL


def _decimal(value: object) -> Decimal:
    try: return Decimal(str(value or "0"))
    except (TypeError, ValueError): return Decimal("0")


def _validate(spec: IntegrationConnectionSpec, capability: IntegrationCapability) -> None:
    if spec.product is not None and str(spec.product) != "equity": raise ValueError("IBKR broker gateway currently supports equity only")
    if spec.access.value != "private" or spec.transport is not TransportKind.REQUEST_API or spec.capability is not capability:
        raise ValueError(f"IBKR gateway requires private request_api {capability.value}")


def _mode_value(value: object) -> str:
    return str(getattr(value, "value", value)).strip().lower()


__all__ = ["IBKRAccountConnection", "IBKRAccountGateway", "IBKRAccountStreamConnection", "IBKRAccountStreamGateway", "IBKRAccountValue", "IBKRExecutionConnection", "IBKRExecutionEvent", "IBKRExecutionGateway", "IBKRExecutionStreamConnection", "IBKRExecutionStreamGateway", "IBKRGatewayDriver", "IBKROpenOrder", "IBKRPosition"]
