import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kairospy.application.usecases.earn.application import EarnApplication
from kairospy.application.usecases.earn.domain import EarnProductType, EarnRedeemRequest, EarnSubscribeRequest
from kairospy.domain.account import AccountModel, AccountSegment, AccountRuntimeContext, Environment, ProductFamily
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
from kairospy.infrastructure.integrations.services.gateways.binance.options.public_rest import BinanceOptionsPublicStreamConnection
from kairospy.infrastructure.integrations.services.drivers.websocket import WebSocketDriver
from kairospy.application.usecases.market.application.integration import MarketFeedSubscriptionRequest
from kairospy.domain.market import Quote
from kairospy.domain.reference import MarketRef
from kairospy.application.usecases.reference.application.requests import ReferenceCatalogRequest
from kairospy.application.support.composition.application.integrations import connect_binance_options_account, connect_binance_options_execution
from kairospy.infrastructure.integrations.services.gateways.binance.equity.private_rest import BinanceEquityExecutionConnection
from kairospy.domain.reference import AssetType


class FakeBinanceClient:
    def __init__(self):
        self.calls = []

    def get(self, path, *, params=None, signed=False):
        self.calls.append(("GET", path, params, signed))
        if path == "/eapi/v1/ticker":
            return [{"symbol": "BTC-260925-60000-C", "bidPrice": "100", "askPrice": "105", "timestamp": 1000}]
        if path == "/eapi/v1/exchangeInfo":
            return {"optionSymbols": [
                {"symbol": "BTC-260925-60000-C", "underlying": "BTC", "expiryDate": 1790000000000, "strikePrice": "60000", "side": "CALL", "unit": "1"},
                {"symbol": "ETH-260925-3000-P", "underlying": "ETH", "expiryDate": 1790000000000, "strikePrice": "3000", "side": "PUT", "unit": "1"},
            ]}
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
    earn = registry.create(_spec(None, IntegrationCapability.EARN, AccessScope.PRIVATE))
    assert isinstance(options, BinanceOptionsPublicRestConnection)
    assert isinstance(earn, BinanceSimpleEarnConnection)


def test_binance_options_private_composition_routes_account_and_execution() -> None:
    account = connect_binance_options_account("binance-options-account", credential="read", mode="paper")
    execution = connect_binance_options_execution("binance-options-execution", credential="trade", mode="paper")

    assert isinstance(account, BinanceOptionsAccountConnection)
    assert isinstance(execution, BinanceOptionsExecutionConnection)


def test_binance_options_native_stream_subscribes_book_ticker_and_normalizes_quote() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.messages: asyncio.Queue[object] = asyncio.Queue()

        async def send(self, message: str) -> None:
            self.sent.append(message)

        def __aiter__(self):
            return self

        async def __anext__(self):
            value = await self.messages.get()
            if value is StopAsyncIteration:
                raise StopAsyncIteration
            return value

        async def close(self) -> None:
            return None

    async def scenario() -> tuple[object, list[str]]:
        session = FakeSession()
        driver = WebSocketDriver(connector=lambda _url: _connected(session))
        connection = BinanceOptionsPublicStreamConnection(
            _spec(ProductFamily.OPTIONS, IntegrationCapability.MARKET_STREAM),
            driver=driver,
        )
        market = MarketRef.ephemeral(venue="binance", market="options", source_symbol="BTC-260925-60000-C")
        subscription = await connection.subscribe(MarketFeedSubscriptionRequest(market, Quote.select(), "test"))
        await session.messages.put({
            "stream": "btc-260925-60000-c@bookTicker",
            "data": {"E": 1000, "s": "BTC-260925-60000-C", "b": "100", "B": "2", "a": "105", "A": "3"},
        })
        event = await anext(subscription.events())
        await subscription.close()
        return event.value, session.sent

    async def _connected(session):
        return session

    value, sent = asyncio.run(scenario())
    assert value.bid == Decimal("100")
    assert value.ask == Decimal("105")
    assert '"method": "SUBSCRIBE"' in sent[0]
    assert "btc-260925-60000-c@bookTicker" in sent[0]
    assert any('"method": "UNSUBSCRIBE"' in message for message in sent)


def test_registry_creates_binance_equity_execution_gateway() -> None:
    connection = GatewayRegistry.with_builtins().create(
        _spec(ProductFamily.SPOT, IntegrationCapability.ORDER_ENTRY, AccessScope.PRIVATE)
    )
    # The generic private Spot spec is intentionally crypto unless the caller
    # asks for the equity asset type explicitly.
    equity = GatewayRegistry.with_builtins().create(
        IntegrationConnectionSpec(
            "binance-equity-execution",
            IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
            ProductFamily.SPOT,
            AccessScope.PRIVATE,
            TransportKind.REST,
            asset_type=AssetType.EQUITY,
            capability=IntegrationCapability.ORDER_ENTRY,
            mode="paper",
        )
    )
    assert not isinstance(connection, BinanceEquityExecutionConnection)
    assert isinstance(equity, BinanceEquityExecutionConnection)


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
    assert len(connection.contracts(underlying="ETH")) == 1
    catalog = connection.catalog(ReferenceCatalogRequest(datetime(2026, 1, 1, tzinfo=timezone.utc), market="options", underlying="BTC"))
    assert len(catalog.markets()) == 1


def test_binance_options_rejects_malformed_exchange_info_instead_of_returning_empty_catalog() -> None:
    class MalformedClient(FakeBinanceClient):
        def get(self, path, *, params=None, signed=False):
            if path == "/eapi/v1/exchangeInfo":
                return {}
            return super().get(path, params=params, signed=signed)

    connection = BinanceOptionsPublicRestConnection(_spec(ProductFamily.OPTIONS, IntegrationCapability.MARKET_DATA))
    connection.operations = BinanceOptionsMarketOperations(MalformedClient())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="no optionSymbols list"):
        connection.contracts()


def test_binance_options_account_and_execution_are_typed_ports() -> None:
    fake = FakeBinanceClient()
    account = AccountRuntimeContext(AccountSegment("binance", "main", AccountModel.CONTRACT, ProductFamily.OPTIONS), Environment.PAPER)
    account_connection = BinanceOptionsAccountConnection(_spec(ProductFamily.OPTIONS, IntegrationCapability.ACCOUNT_READ, AccessScope.PRIVATE))
    account_connection.operations.client = fake  # type: ignore[assignment]
    snapshot = account_connection.read_account(ConnectionAccountReadRequest(account, datetime.now(timezone.utc))).snapshot
    assert snapshot.balances[0].currency == "USDT"
    execution = BinanceOptionsExecutionConnection(_spec(ProductFamily.OPTIONS, IntegrationCapability.ORDER_ENTRY, AccessScope.PRIVATE))
    execution.operations.client = fake  # type: ignore[assignment]
    result = execution.submit(ConnectionOrderSubmissionRequest(account.segment, "BTC-260925-60000-C", OrderSide.BUY, OrderType.LIMIT, Decimal("1"), Decimal("100")))
    assert result.order_venue_id == ""  # fake public client is intentionally not an order response


def test_binance_equity_limit_order_maps_to_stocks_trading_api() -> None:
    fake = FakeBinanceClient()
    connection = BinanceEquityExecutionConnection(
        IntegrationConnectionSpec(
            "binance-equity-execution-test",
            IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
            ProductFamily.SPOT,
            AccessScope.PRIVATE,
            TransportKind.REST,
            asset_type=AssetType.EQUITY,
            capability=IntegrationCapability.ORDER_ENTRY,
            mode="paper",
        )
    )
    connection.order_operations.client = fake  # type: ignore[assignment]
    result = connection.submit(ConnectionOrderSubmissionRequest(
        AccountSegment("binance", "main", AccountModel.NO_MARGIN, ProductFamily.SPOT),
        "SPY",
        OrderSide.BUY,
        OrderType.LIMIT,
        Decimal("1"),
        Decimal("750"),
        asset_type=AssetType.EQUITY,
    ))
    assert result.order_venue_id == ""
    call = next(call for call in fake.calls if call[0] == "POST")
    assert call[1] == "/sapi/v1/equity/order/place"
    assert call[2]["symbol"] == "SPY"
    assert call[2]["side"] == "BUY"
    assert call[2]["orderType"] == "LIMIT"
    assert call[2]["price"] == "750"
    assert call[2]["quantity"] == "1"
    assert call[2]["tradingSession"] == "RTH"
    assert call[2]["walletType"] == "MAIN"


def test_simple_earn_application_uses_replaceable_provider() -> None:
    connection = BinanceSimpleEarnConnection(_spec(None, IntegrationCapability.EARN, AccessScope.PRIVATE))
    fake = FakeBinanceClient()
    connection.client = fake  # type: ignore[assignment]
    application = EarnApplication(connection)
    assert application.list_products()[0].product_id == "P-1"
    assert application.positions()[0].principal == Decimal("10")
    assert application.positions()[0].accrued_reward == Decimal("0")
    assert application.positions()[0].apr == Decimal("0")
    assert application.rewards()[0].amount == Decimal("0.1")
    assert application.subscribe(EarnSubscribeRequest("P-1", Decimal("2"))) == {"success": True}
    assert application.redeem(EarnRedeemRequest("P-1", Decimal("1"))) == {"success": True}


def test_simple_earn_locked_routes_are_distinct_from_flexible() -> None:
    connection = BinanceSimpleEarnConnection(_spec(None, IntegrationCapability.EARN, AccessScope.PRIVATE))
    fake = FakeBinanceClient()
    connection.client = fake  # type: ignore[assignment]
    connection.subscribe(EarnSubscribeRequest("L-1", Decimal("2"), product_type=EarnProductType.LOCKED))
    connection.redeem(EarnRedeemRequest("L-1", Decimal("1"), product_type=EarnProductType.LOCKED))
    assert [call[1] for call in fake.calls if call[0] == "POST"][-2:] == [
        "/sapi/v1/simple-earn/locked/subscribe",
        "/sapi/v1/simple-earn/locked/redeem",
    ]
