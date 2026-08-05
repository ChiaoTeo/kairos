from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from dataclasses import replace
from decimal import Decimal

from kairospy.domain.account import AccountBookRef, AccountContext, Environment
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.order import OrderSide, OrderType
from kairospy.infrastructure.integrations.application.account import ConnectionAccountReadRequest, ConnectionAccountStreamRequest
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec, RuntimeMode
from kairospy.infrastructure.integrations.application.execution import ConnectionOrderCancelRequest, ConnectionOrderSubmissionRequest
from kairospy.infrastructure.integrations.domain import AccessScope, BrokerId, BrokerRef, IntegrationCapability, IntegrationRoute, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.gateways.ibkr.execution import (
    IBKRAccountConnection,
    IBKRAccountStreamConnection,
    IBKRAccountValue,
    IBKRExecutionConnection,
    IBKRExecutionEvent,
    IBKRExecutionStreamConnection,
    IBKROpenOrder,
    IBKRPosition,
)
from kairospy.infrastructure.integrations.services.factories.registry import GatewayRegistry
from kairospy.application.support.composition.application.resources import account_read_access, execution_access, private_account_access
from kairospy.application.usecases.market.application.commands.resources import DriverName


class _FakeGateway:
    def __init__(self) -> None:
        self.started = False
        self.submissions: list[dict[str, object]] = []
        self.cancellations: list[str] = []
        self.values = (IBKRAccountValue("TotalCashValue", Decimal("10000")), IBKRAccountValue("AvailableFunds", Decimal("8000")), IBKRAccountValue("MaintMarginReq", Decimal("500")))

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def reconnect(self) -> None:
        self.started = True

    def submit(self, **kwargs):
        self.submissions.append(kwargs)
        return "42", "Submitted"

    def cancel(self, *, order_venue_id: str) -> str:
        self.cancellations.append(order_venue_id)
        return "Canceled"

    def account_values(self, *, account_id: str):
        return self.values

    def positions(self, *, account_id: str):
        return (IBKRPosition("AAPL", Decimal("2"), Decimal("180")),)

    def open_orders(self, *, account_id: str):
        return (IBKROpenOrder("43", "MSFT", OrderSide.SELL, Decimal("3")),)

    async def execution_events(self, *, account_id: str, symbol: str | None = None):
        yield IBKRExecutionEvent("42", "AAPL", "Filled", OrderSide.BUY, OrderType.LIMIT, Decimal("2"), Decimal("190"), Decimal("2"), Decimal("0"), Decimal("2"), Decimal("190.25"), datetime.now(timezone.utc))


def _spec() -> IntegrationConnectionSpec:
    return IntegrationConnectionSpec(
        connection_id="ibkr-paper-equity",
        route=IntegrationRoute(broker=BrokerRef(BrokerId.IBKR)),
        product=ProductFamily.EQUITY,
        access=AccessScope.PRIVATE,
        transport=TransportKind.REQUEST_API,
        mode=RuntimeMode.PAPER,
    )


def test_registry_selects_ibkr_as_private_equity_broker() -> None:
    connection = GatewayRegistry.with_builtins().create(_spec())
    assert isinstance(connection, IBKRExecutionConnection)


def test_registry_selects_explicit_ibkr_account_and_execution_stream_connections() -> None:
    registry = GatewayRegistry.with_builtins()
    account = registry.create(replace(_spec(), capability=IntegrationCapability.ACCOUNT_READ))
    stream = registry.create(replace(_spec(), capability=IntegrationCapability.EXECUTION_STREAM))
    account_stream = registry.create(replace(_spec(), capability=IntegrationCapability.ACCOUNT_STREAM))
    assert isinstance(account, IBKRAccountConnection)
    assert isinstance(stream, IBKRExecutionStreamConnection)
    assert isinstance(account_stream, IBKRAccountStreamConnection)


def test_ibkr_order_connection_maps_canonical_order_dtos() -> None:
    gateway = _FakeGateway()
    connection = IBKRExecutionConnection(_spec(), gateway=gateway)
    account = AccountBookRef("ibkr", "paper-account", "equity")
    result = connection.submit(
        ConnectionOrderSubmissionRequest(
            account=account,
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("2"),
            limit_price=Decimal("190.25"),
        )
    )
    canceled = connection.cancel(ConnectionOrderCancelRequest(account=account, order_venue_id=result.order_venue_id, symbol="AAPL"))

    assert result.order_venue_id == "42"
    assert canceled.status == "Canceled"
    assert gateway.submissions[0]["symbol"] == "AAPL"
    assert gateway.submissions[0]["limit_price"] == Decimal("190.25")
    assert gateway.cancellations == ["42"]


def test_ibkr_connection_adapts_sync_driver_lifecycle() -> None:
    gateway = _FakeGateway()
    connection = IBKRExecutionConnection(_spec(), gateway=gateway)
    asyncio.run(connection.start())
    assert gateway.started
    asyncio.run(connection.stop())
    assert not gateway.started


def test_ibkr_account_connection_maps_account_values_positions_and_orders() -> None:
    gateway = _FakeGateway()
    connection = IBKRAccountConnection(replace(_spec(), capability=IntegrationCapability.ACCOUNT_READ), gateway=gateway)
    context = AccountContext(AccountBookRef("ibkr", "paper-account", "equity"), Environment.PAPER)
    snapshot = connection.read_account(ConnectionAccountReadRequest(context=context, observed_at=datetime.now(timezone.utc))).snapshot
    assert snapshot.balances[0].free == Decimal("8000")
    assert snapshot.positions[0].quantity == Decimal("2")
    assert snapshot.open_orders[0].quantity == Decimal("3")


def test_ibkr_execution_stream_maps_fill_to_domain_update() -> None:
    spec = IntegrationConnectionSpec(connection_id="ibkr-stream", route=IntegrationRoute(broker=BrokerRef(BrokerId.IBKR)), product=ProductFamily.EQUITY, access=AccessScope.PRIVATE, transport=TransportKind.REQUEST_API, mode=RuntimeMode.PAPER, capability=IntegrationCapability.EXECUTION_STREAM)
    connection = IBKRExecutionStreamConnection(spec, gateway=_FakeGateway())
    context = AccountContext(AccountBookRef("ibkr", "paper-account", "equity"), Environment.PAPER)

    async def read_one() -> ExecutionUpdate:
        async for update in connection.execution_updates(ConnectionAccountStreamRequest(context=context)):
            return update
        raise AssertionError("stream ended before producing an update")

    update = asyncio.run(read_one())
    assert update.kind.value == "filled"
    assert update.fill_price == Decimal("190.25")


def test_ibkr_broker_is_selected_by_composition_for_account_and_execution() -> None:
    book = AccountBookRef("ibkr", "paper-account", "equity")
    assert isinstance(private_account_access(book, DriverName.ibkr), IBKRAccountConnection)
    assert isinstance(account_read_access(book, DriverName.ibkr), IBKRAccountConnection)
    assert isinstance(execution_access(book, DriverName.ibkr), IBKRExecutionConnection)
