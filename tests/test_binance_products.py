from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.usecases.earn.application import EarnApplication
from kairospy.application.usecases.earn.domain import EarnRedeemRequest, EarnSubscribeRequest
from kairospy.domain.account import AccountBookKind, AccountBookRef, AccountContext, Environment
from kairospy.domain.order import OrderSide, OrderType
from kairospy.infrastructure.integrations.application.account import ConnectionAccountReadRequest
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.execution import ConnectionOrderSubmissionRequest
from kairospy.infrastructure.integrations.domain import AccessScope, BrokerId, BrokerRef, ExchangeId, ExchangeRef, IntegrationCapability, IntegrationRoute, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.factories.registry import GatewayRegistry
from kairospy.infrastructure.integrations.services.gateways.binance.earn import BinanceSimpleEarnConnection
from kairospy.infrastructure.integrations.services.gateways.binance.options.operations import BinanceOptionsMarketOperations
from kairospy.infrastructure.integrations.services.gateways.binance.options.private_rest import BinanceOptionsAccountConnection, BinanceOptionsExecutionConnection
from kairospy.infrastructure.integrations.services.gateways.binance.options.public_rest import BinanceOptionsPublicRestConnection


class FakeBinanceClient:
    def __init__(self):
        self.calls = []

    def get(self, path, *, params=None, signed=False):
        self.calls.append(("GET", path, params, signed))
        if path == "/eapi/v1/ticker":
            return [{"symbol": "BTC-260925-60000-C", "bidPrice": "100", "askPrice": "105", "timestamp": 1000}]
        if path == "/eapi/v1/exchangeInfo":
            return {"optionSymbols": [{"symbol": "BTC-260925-60000-C", "underlying": "BTC", "expiryDate": 1790000000000, "strikePrice": "60000", "side": "CALL", "unit": "1"}]}
        if path == "/sapi/v1/simple-earn/products":
            return {"rows": [{"productId": "P-1", "asset": "USDT", "productType": "FLEXIBLE", "latestAnnualPercentageRate": "0.05"}]}
        if path == "/sapi/v1/simple-earn/positions":
            return {"rows": [{"productId": "P-1", "asset": "USDT", "totalAmount": "10", "status": "HOLDING"}]}
        if path == "/sapi/v1/simple-earn/rewardsRecord":
            return {"rows": [{"productId": "P-1", "asset": "USDT", "rewardsAmount": "0.1", "time": 1000}]}
        if path == "/eapi/v1/account":
            return {"assets": [{"asset": "USDT", "available": "100", "freeze": "2"}], "positions": []}
        if path == "/eapi/v1/openOrders":
            return []
        raise AssertionError(path)

    def post(self, path, *, params=None, signed=True):
        self.calls.append(("POST", path, params, signed))
        return {"success": True}

    def delete(self, path, *, params=None, signed=True):
        self.calls.append(("DELETE", path, params, signed))
        return {"orderId": "O-1", "status": "CANCELED"}


def _spec(product, capability, access=AccessScope.PUBLIC):
    route = IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)) if access is AccessScope.PUBLIC else IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE))
    return IntegrationConnectionSpec("binance-test", route, product, access, TransportKind.REST, capability=capability, mode="paper")


def test_registry_has_distinct_binance_options_and_earn_routes() -> None:
    registry = GatewayRegistry.with_builtins()
    options = registry.create(_spec(ProductFamily.OPTIONS, IntegrationCapability.MARKET_DATA))
    earn = registry.create(_spec(ProductFamily.EARN, IntegrationCapability.EARN, AccessScope.PRIVATE))
    assert isinstance(options, BinanceOptionsPublicRestConnection)
    assert isinstance(earn, BinanceSimpleEarnConnection)


def test_binance_options_public_quote_is_normalized() -> None:
    connection = BinanceOptionsPublicRestConnection(_spec(ProductFamily.OPTIONS, IntegrationCapability.MARKET_DATA))
    fake = FakeBinanceClient()
    connection.operations = BinanceOptionsMarketOperations(fake)  # type: ignore[arg-type]
    quote = connection.latest_quote("BTC-260925-60000-C")
    assert quote is not None
    assert quote.bid == Decimal("100")
    assert quote.ask == Decimal("105")
    contracts = connection.contracts(underlying="BTC")
    assert contracts[0].right == "call"
    assert contracts[0].strike == Decimal("60000")


def test_binance_options_account_and_execution_are_typed_ports() -> None:
    fake = FakeBinanceClient()
    account = AccountContext(AccountBookRef("binance", "main", AccountBookKind.OPTIONS), Environment.PAPER)
    account_connection = BinanceOptionsAccountConnection(_spec(ProductFamily.OPTIONS, IntegrationCapability.ACCOUNT_READ, AccessScope.PRIVATE))
    account_connection.operations.client = fake  # type: ignore[assignment]
    snapshot = account_connection.read_account(ConnectionAccountReadRequest(account, datetime.now(timezone.utc))).snapshot
    assert snapshot.balances[0].currency == "USDT"
    execution = BinanceOptionsExecutionConnection(_spec(ProductFamily.OPTIONS, IntegrationCapability.ORDER_ENTRY, AccessScope.PRIVATE))
    execution.operations.client = fake  # type: ignore[assignment]
    result = execution.submit(ConnectionOrderSubmissionRequest(account.book, "BTC-260925-60000-C", OrderSide.BUY, OrderType.LIMIT, Decimal("1"), Decimal("100")))
    assert result.order_venue_id == ""  # fake public client is intentionally not an order response


def test_simple_earn_application_uses_replaceable_provider() -> None:
    connection = BinanceSimpleEarnConnection(_spec(ProductFamily.EARN, IntegrationCapability.EARN, AccessScope.PRIVATE))
    fake = FakeBinanceClient()
    connection.client = fake  # type: ignore[assignment]
    application = EarnApplication(connection)
    assert application.list_products()[0].product_id == "P-1"
    assert application.positions()[0].principal == Decimal("10")
    assert application.rewards()[0].amount == Decimal("0.1")
    assert application.subscribe(EarnSubscribeRequest("P-1", Decimal("2"))) == {"success": True}
    assert application.redeem(EarnRedeemRequest("P-1", Decimal("1"))) == {"success": True}
