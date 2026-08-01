from __future__ import annotations

import asyncio
import hmac
import hashlib
import importlib.util
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kairospy.application.ports import MarketDataSubscriptionSpec
from kairospy.application.service.modes.common.integrations import default_broker_for_book, default_market_feed_for_subscription
from kairospy.application.domain.account.routing import account_book_route
from kairospy.core.account import AccountContext, Environment
from kairospy.core.order import OrderEventKind, OrderSide, OrderType
from kairospy.core.account import AccountBookRef
from kairospy.core.market import Quote
from kairospy.core.reference import MarketRef
from kairospy.infrastructure.integrations.connectors.broker.binance import (
    BinanceBroker,
    BinanceEquityBroker,
    BinanceEquityMarketDataConnector,
    BinanceEquityReferenceConnector,
    BinanceSapiClient,
    BinanceSapiError,
)
from kairospy.infrastructure.integrations.payloads.binance_equity_execution import (
    binance_equity_order_update,
    binance_equity_trade_update,
)
from kairospy.infrastructure.integrations.resolver import IntegrationResolver, ReferenceSourceRef


class FakeResponse:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)
        self.content = b"{}"

    def json(self) -> object:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, *, params: dict[str, object], headers: dict[str, str], timeout: int) -> FakeResponse:
        self.calls.append({"method": method, "url": url, "params": params, "headers": headers, "timeout": timeout})
        return self.response


def test_sapi_client_signs_requests_with_api_key_header() -> None:
    session = FakeSession(FakeResponse({"ok": True}))
    client = BinanceSapiClient(api_key="api-key", secret="secret", session=session, time_provider=lambda: 1710320400000)

    assert client.get("/sapi/v1/equity/order/open", params={"symbol": "AAPL"}, signed=True) == {"ok": True}

    call = session.calls[0]
    params = call["params"]
    assert call["method"] == "GET"
    assert call["url"] == "https://api.binance.com/sapi/v1/equity/order/open"
    assert call["headers"] == {"X-MBX-APIKEY": "api-key"}
    assert params["symbol"] == "AAPL"
    assert params["recvWindow"] == 5000
    assert params["timestamp"] == 1710320400000
    query = "symbol=AAPL&recvWindow=5000&timestamp=1710320400000"
    expected = hmac.new(b"secret", query.encode("utf-8"), hashlib.sha256).hexdigest()
    assert params["signature"] == expected


def test_sapi_client_raises_structured_error_payload() -> None:
    session = FakeSession(FakeResponse({"code": 486410, "msg": "sign disclaimer first"}, status_code=400))
    client = BinanceSapiClient(api_key="api-key", session=session)

    with pytest.raises(BinanceSapiError) as error:
        client.get("/sapi/v1/equity/market/exchangeInfo")

    assert error.value.code == 486410
    assert error.value.status_code == 400
    assert str(error.value) == "sign disclaimer first"


def test_equity_reference_connector_maps_exchange_info_to_catalog() -> None:
    session = FakeSession(
        FakeResponse(
            {
                "symbols": [
                    {
                        "symbol": "AAPL",
                        "tradability": "BUY_SELL",
                        "stepSize": "0.000001",
                        "minQty": "0.000001",
                        "minNotional": "1",
                        "fractionable": True,
                        "extendedSession": True,
                    }
                ]
            }
        )
    )
    connector = BinanceEquityReferenceConnector(BinanceSapiClient(api_key="api-key", session=session))
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)

    rows = tuple(connector.fetch_markets())
    catalog = connector.fetch_reference_catalog(as_of=as_of)
    market = catalog.resolve_market("AAPL", venue="binance", market="equity", at=as_of)

    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["settlement_asset"] == "USDC"
    assert str(market.instrument_id) == "instrument:equity:aapl"
    assert market.source_symbol == "AAPL"
    assert market.price_tick == Decimal("0.01")
    assert market.amount_tick == Decimal("0.000001")
    assert market.min_notional == Decimal("1")
    assert market.metadata["tradability"] == "BUY_SELL"
    assert market.metadata["raw"]["symbol"] == "AAPL"


def test_default_broker_for_book_routes_binance_equity_to_broker_connector() -> None:
    equity = default_broker_for_book(AccountBookRef("binance", "main", "equity"), None, mode_label="live", error_type=RuntimeError)
    spot = default_broker_for_book(AccountBookRef("binance", "main", "spot"), None, mode_label="live", error_type=RuntimeError)

    assert isinstance(equity, BinanceEquityBroker)
    assert isinstance(spot, BinanceBroker)


def test_binance_broker_has_no_exchange_reexport_module() -> None:
    assert importlib.util.find_spec("kairospy.infrastructure.integrations.connectors.exchange.binance.broker") is None


def test_default_market_feed_for_subscription_routes_binance_equity_to_broker_product_connector() -> None:
    spec = MarketDataSubscriptionSpec(MarketRef.ephemeral(venue="binance", market="equity", source_symbol="AAPL"), (Quote,))

    feed = default_market_feed_for_subscription(spec, mode_label="live", error_type=RuntimeError)

    assert isinstance(feed.feed, BinanceEquityMarketDataConnector)


def test_integration_resolver_routes_by_capability_and_source() -> None:
    resolver = IntegrationResolver()
    spec = MarketDataSubscriptionSpec(MarketRef.ephemeral(venue="binance", market="equity", source_symbol="AAPL"), (Quote,))

    assert isinstance(resolver.market_feed_for_subscription(spec), BinanceEquityMarketDataConnector)
    assert isinstance(resolver.account_bootstrap_for_book(AccountBookRef("binance", "main", "equity")), BinanceEquityBroker)
    assert isinstance(resolver.order_query_for_book(AccountBookRef("binance", "main", "equity")), BinanceEquityBroker)
    assert isinstance(resolver.order_execution_for_book(AccountBookRef("binance", "main", "spot")), BinanceBroker)
    assert isinstance(resolver.reference_data(ReferenceSourceRef("broker", "binance", book="equity")), BinanceEquityReferenceConnector)


def test_integration_resolver_rejects_binance_equity_order_execution_until_supported() -> None:
    resolver = IntegrationResolver()

    with pytest.raises(RuntimeError, match="unsupported live order execution book"):
        resolver.order_execution_for_book(AccountBookRef("binance", "main", "equity"), mode_label="live", error_type=RuntimeError)


def test_equity_market_data_connector_polls_quote_as_ticker_stream() -> None:
    session = FakeSession(FakeResponse({"bp": "180.10", "ap": "180.20", "bs": "4", "as": "5", "T": 1710320400000}))
    connector = BinanceEquityMarketDataConnector(BinanceSapiClient(api_key="api-key", session=session))

    event = asyncio.run(_first(connector.watch_ticker("aapl", params={"max_events": 1, "poll_seconds": 0})))

    assert event["source_symbol"] == "AAPL"
    assert event["bid"] == Decimal("180.10")
    assert event["ask"] == Decimal("180.20")
    assert session.calls[0]["url"] == "https://api.binance.com/sapi/v1/equity/market/quote"
    assert session.calls[0]["params"] == {"symbol": "AAPL"}


def test_binance_equity_route_is_read_only_until_order_translation_exists() -> None:
    route = account_book_route(AccountBookRef("binance", "main", "equity"))

    assert route.can_trade is False


def test_equity_broker_places_limit_order_with_stock_fields() -> None:
    session = FakeSession(FakeResponse({"orderId": 123, "status": "NEW"}))
    broker = BinanceEquityBroker(BinanceSapiClient(api_key="api-key", secret="secret", session=session, time_provider=lambda: 1710320400000))

    response = broker.create_order(
        "aapl",
        side="buy",
        type="limit",
        amount=Decimal("1.25"),
        price=Decimal("180.123"),
        params={"trading_session": "REGULAR", "client_order_id": "client-1"},
    )

    assert response["id"] == "123"
    assert response["orderId"] == 123
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.binance.com/sapi/v1/equity/order/place"
    params = call["params"]
    assert params["symbol"] == "AAPL"
    assert params["side"] == "BUY"
    assert params["orderType"] == "LIMIT"
    assert params["quantity"] == "1.25"
    assert params["price"] == "180.12"
    assert params["tradingSession"] == "REGULAR"
    assert params["clientOrderId"] == "client-1"
    assert "signature" in params


def test_equity_broker_requires_notional_for_market_buy() -> None:
    broker = BinanceEquityBroker(BinanceSapiClient(api_key="api-key", secret="secret", session=FakeSession(FakeResponse({}))))

    with pytest.raises(ValueError, match="requires notional"):
        broker.create_order("AAPL", side="buy", type="market", amount=Decimal("1"))


def test_equity_broker_places_market_buy_with_notional() -> None:
    session = FakeSession(FakeResponse({"orderId": "buy-1", "status": "ACCEPTED"}))
    broker = BinanceEquityBroker(BinanceSapiClient(api_key="api-key", secret="secret", session=session, time_provider=lambda: 1710320400000))

    broker.create_order("AAPL", side="buy", type="market", amount=Decimal("1"), params={"notional": Decimal("250"), "quote_asset": "USDC"})

    params = session.calls[0]["params"]
    assert params["orderType"] == "MARKET"
    assert params["side"] == "BUY"
    assert params["notional"] == "250"
    assert params["quoteAsset"] == "USDC"
    assert "quantity" not in params


def test_equity_broker_places_market_sell_with_quantity() -> None:
    session = FakeSession(FakeResponse({"orderId": "sell-1", "status": "ACCEPTED"}))
    broker = BinanceEquityBroker(BinanceSapiClient(api_key="api-key", secret="secret", session=session, time_provider=lambda: 1710320400000))

    broker.create_order("AAPL", side="sell", type="market", amount=Decimal("0.5"))

    params = session.calls[0]["params"]
    assert params["side"] == "SELL"
    assert params["orderType"] == "MARKET"
    assert params["quantity"] == "0.5"
    assert "notional" not in params


def test_equity_broker_cancels_and_fetches_detail_and_trade_history() -> None:
    session = FakeSession(FakeResponse({"orderId": "order-1", "status": "CANCELED", "trades": [{"executionId": "fill-1"}]}))
    broker = BinanceEquityBroker(BinanceSapiClient(api_key="api-key", secret="secret", session=session, time_provider=lambda: 1710320400000))

    assert broker.cancel_order("order-1", symbol="aapl")["status"] == "CANCELED"
    assert broker.fetch_order_detail("order-1", symbol="aapl")["orderId"] == "order-1"
    assert tuple(broker.fetch_trade_history("aapl", order_id="order-1")) == ({"executionId": "fill-1"},)

    assert session.calls[0]["url"].endswith("/sapi/v1/equity/order/cancel")
    assert session.calls[0]["params"]["orderId"] == "order-1"
    assert session.calls[0]["params"]["symbol"] == "AAPL"
    assert session.calls[1]["url"].endswith("/sapi/v1/equity/order/detail")
    assert session.calls[2]["url"].endswith("/sapi/v1/equity/trade/history")


def test_binance_equity_order_update_maps_status_and_identity() -> None:
    context = AccountContext(AccountBookRef("binance", "main", "equity"), Environment.LIVE)

    update = binance_equity_order_update(
        context,
        {
            "orderId": "order-1",
            "symbol": "AAPL",
            "side": "BUY",
            "orderType": "LIMIT",
            "limitPrice": "180.12",
            "qty": "2",
            "filledQty": "0.5",
            "status": "PARTIALLY_FILLED",
            "updatedAt": 1710320400000,
        },
    )

    assert update.kind is OrderEventKind.PARTIALLY_FILLED
    assert update.order_venue_id == "order-1"
    assert update.instrument_id == "instrument:equity:aapl"
    assert update.market_id == "market:binance:equity:aapl"
    assert update.side is OrderSide.BUY
    assert update.order_type is OrderType.LIMIT
    assert update.limit_price == Decimal("180.12")
    assert update.quantity == Decimal("2")
    assert update.filled_quantity == Decimal("0.5")
    assert update.remaining_quantity == Decimal("1.5")


def test_binance_equity_trade_update_maps_fill_cash_and_fee() -> None:
    context = AccountContext(AccountBookRef("binance", "main", "equity"), Environment.LIVE)

    update = binance_equity_trade_update(
        context,
        {
            "orderId": "order-1",
            "executionId": "fill-1",
            "symbol": "AAPL",
            "side": "BUY",
            "price": "180",
            "qty": "0.5",
            "fee": "0.01",
            "quote": "USDC",
            "executionAt": 1710320400000,
        },
    )

    assert update.kind is OrderEventKind.PARTIALLY_FILLED
    assert update.fill_quantity == Decimal("0.5")
    assert update.fill_price == Decimal("180")
    assert update.cash_delta == Decimal("-90.0")
    assert update.fee_currency == "USDC"
    assert update.fee_amount == Decimal("0.01")


async def _first(events):
    async for event in events:
        return event
    raise AssertionError("stream produced no events")
