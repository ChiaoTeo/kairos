from __future__ import annotations

from decimal import Decimal

from kairospy.domain.account import AccountBookRef
from kairospy.domain.order import OrderSide, OrderType
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec, RuntimeMode
from kairospy.infrastructure.integrations.application.execution import ConnectionOrderCancelRequest, ConnectionOrderSubmissionRequest
from kairospy.infrastructure.integrations.domain import AccessScope, BrokerId, BrokerRef, IntegrationRoute, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.gateways.ibkr.execution import IBKRExecutionConnection
from kairospy.infrastructure.integrations.services.factories.registry import GatewayRegistry


class _FakeGateway:
    def __init__(self) -> None:
        self.started = False
        self.submissions: list[dict[str, object]] = []
        self.cancellations: list[str] = []

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
