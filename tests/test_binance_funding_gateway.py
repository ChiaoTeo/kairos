from datetime import datetime, timezone
from decimal import Decimal

from kairospy.domain.account import AccountModel, AccountSegment, AccountRuntimeContext, Environment, ExternalAccountIdentity
from kairospy.infrastructure.integrations.application.account import ConnectionAccountReadRequest
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import AccessScope, BrokerId, BrokerRef, IntegrationCapability, IntegrationRoute, TransportKind
from kairospy.infrastructure.integrations.services.gateways.binance.funding import BinanceFundingAccountConnection


class _Response:
    status_code = 200
    content = b"[]"
    text = "[]"

    @staticmethod
    def json() -> object:
        return [
            {"asset": "USDT", "free": "12.5", "locked": "1.5"},
            {"asset": "BTC", "free": "0.1", "freeze": "0.01"},
        ]


class _Driver:
    def request(self, method: str, url: str, *, params: dict[str, object], headers: dict[str, str]) -> _Response:
        assert method == "POST"
        assert url.endswith("/sapi/v1/asset/get-funding-asset")
        return _Response()


def test_binance_funding_wallet_is_a_read_only_account_snapshot() -> None:
    spec = IntegrationConnectionSpec(
        connection_id="test.binance.funding",
        route=IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
        product=None,
        access=AccessScope.PRIVATE,
        transport=TransportKind.REST,
        capability=IntegrationCapability.ACCOUNT_READ,
        credential=None,
        mode="live",
    )
    connection = BinanceFundingAccountConnection(spec)
    connection.client.driver = _Driver()  # type: ignore[assignment]
    connection.client.api_key = "key"
    connection.client.secret = "secret"
    context = AccountRuntimeContext(
        AccountSegment(ExternalAccountIdentity("binance", "main"), "funding", AccountModel.NO_MARGIN),
        Environment.LIVE,
    )

    snapshot = connection.read_account(ConnectionAccountReadRequest(context, datetime.now(timezone.utc))).snapshot

    assert [(item.currency, item.total, item.free, item.locked) for item in snapshot.balances] == [
        ("USDT", Decimal("14.0"), Decimal("12.5"), Decimal("1.5")),
        ("BTC", Decimal("0.11"), Decimal("0.1"), Decimal("0.01")),
    ]
    assert snapshot.positions == ()
    assert snapshot.open_orders == ()
