from __future__ import annotations

import asyncio

from kairospy.infrastructure.integrations import CcxtDriver, OkxBroker, OkxMarketDataConnector
from kairospy.infrastructure.integrations.connectors.exchange.okx.market_data import _okx_config
from kairospy.surface.runtime import DriverName, ExchangeName, exchange


class FakeSyncOkxExchange:
    def __init__(self) -> None:
        self.closed = False

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        assert symbol == "BTC/USDT"
        assert params == {"tdMode": "cash"}
        return {
            "id": "okx-order-1",
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": str(amount),
            "price": str(price),
            "params": params,
        }

    def cancel_order(self, id, symbol=None, params=None):
        assert id == "okx-order-1"
        assert symbol == "BTC/USDT"
        assert params == {"tdMode": "cash"}
        return {"id": id, "symbol": symbol, "status": "canceled", "params": params}

    def fetch_balance(self, params=None):
        assert params == {"type": "spot"}
        return {"free": {"USDT": "90"}, "used": {"USDT": "10"}, "total": {"USDT": "100"}}

    def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        assert since is None
        assert limit == 10
        assert params == {"type": "spot"}
        return [
            {
                "id": "okx-open-1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "type": "limit",
                "amount": "1",
                "remaining": "0.75",
                "filled": "0.25",
                "price": "100",
            }
        ]

    def close(self):
        self.closed = True


class FakeWsOkxExchange:
    async def watch_balance(self, params=None):
        assert params == {"type": "spot"}
        return {"free": {"USDT": "90"}, "used": {"USDT": "10"}, "total": {"USDT": "100"}}

    async def watch_orders(self, symbol=None, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        assert since is None
        assert limit == 10
        assert params == {"type": "spot"}
        return [{"id": "okx-ws-order-1", "symbol": symbol, "side": "buy", "amount": "1"}]

    async def watch_my_trades(self, symbol=None, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        assert since is None
        assert limit == 10
        assert params == {"type": "spot"}
        return [{"id": "okx-ws-fill-1", "order": "okx-ws-order-1", "symbol": symbol, "amount": "1"}]

    async def close(self):
        pass


def _okx_driver() -> CcxtDriver:
    return CcxtDriver(
        exchange_factory=lambda exchange_id: FakeSyncOkxExchange(),
        async_exchange_factory=lambda exchange_id: FakeWsOkxExchange(),
    )


def test_okx_broker_uses_ccxt_driver_for_trading_and_account() -> None:
    broker = OkxBroker(_okx_driver())

    order = broker.create_order(
        "BTC/USDT",
        side="buy",
        type="limit",
        amount="0.1",
        price="100",
        params={"tdMode": "cash"},
    )
    canceled = broker.cancel_order("okx-order-1", symbol="BTC/USDT", params={"tdMode": "cash"})
    balance = broker.fetch_balance(params={"type": "spot"})
    open_orders = tuple(broker.fetch_open_orders("BTC/USDT", limit=10, params={"type": "spot"}))

    assert order["id"] == "okx-order-1"
    assert canceled["status"] == "canceled"
    assert balance["free"]["USDT"] == "90"
    assert open_orders[0]["id"] == "okx-open-1"


def test_okx_broker_exposes_ccxt_pro_account_watch_methods() -> None:
    async def scenario() -> None:
        broker = OkxBroker(_okx_driver())

        balances = [event async for event in broker.watch_balance(params={"type": "spot", "max_events": 1, "poll_seconds": 0})]
        orders = [
            event
            async for event in broker.watch_orders(
                "BTC/USDT",
                limit=10,
                params={"type": "spot", "max_events": 1, "poll_seconds": 0},
            )
        ]
        fills = [
            event
            async for event in broker.watch_my_trades(
                "BTC/USDT",
                limit=10,
                params={"type": "spot", "max_events": 1, "poll_seconds": 0},
            )
        ]

        assert balances[0]["total"]["USDT"] == "100"
        assert orders[0]["id"] == "okx-ws-order-1"
        assert fills[0]["id"] == "okx-ws-fill-1"

    asyncio.run(scenario())


def test_okx_exchange_runtime_constructs_okx_connector_for_okex_alias() -> None:
    okx = exchange(ExchangeName.okx, DriverName.ccxt)
    okex = exchange(ExchangeName.okex, DriverName.ccxt)

    assert isinstance(okx, OkxMarketDataConnector)
    assert isinstance(okex, OkxMarketDataConnector)
    assert okx.exchange_id == "okx"
    assert okex.exchange_id == "okx"


def test_okx_config_reads_credentials_and_proxy_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("OKX_API_KEY", "api-key")
    monkeypatch.setenv("OKX_SECRET", "secret")
    monkeypatch.setenv("OKX_PASSWORD", "password")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7897")
    monkeypatch.delenv("HTTP_PROXY", raising=False)

    config = _okx_config()

    assert config["apiKey"] == "api-key"
    assert config["secret"] == "secret"
    assert config["password"] == "password"
    assert config["proxies"] == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }
    assert config["aiohttp_proxy"] == "http://127.0.0.1:7897"


def test_okx_config_prefers_credential_specific_environment(monkeypatch) -> None:
    monkeypatch.setenv("OKX_API_KEY", "fallback-api-key")
    monkeypatch.setenv("OKX_SECRET", "fallback-secret")
    monkeypatch.setenv("OKX_PASSWORD", "fallback-password")
    monkeypatch.setenv("OKX_MAIN_API_KEY", "main-api-key")
    monkeypatch.setenv("OKX_MAIN_SECRET", "main-secret")
    monkeypatch.setenv("OKX_MAIN_PASSWORD", "main-password")

    config = _okx_config("env:okx-main")

    assert config["apiKey"] == "main-api-key"
    assert config["secret"] == "main-secret"
    assert config["password"] == "main-password"
