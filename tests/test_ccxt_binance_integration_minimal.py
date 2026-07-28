from __future__ import annotations

import asyncio
from tempfile import TemporaryDirectory

from kairospy.infrastructure.data import DataStore
from kairospy.infrastructure.integrations import BinanceBroker, BinanceMarketDataConnector, CcxtDriver
from kairospy.infrastructure.integrations.payloads import ccxt_ticker_update, ephemeral_market_ref
from kairospy.infrastructure.data import DataSink
from kairospy.core.market import Bar, MarketEvent, Quote


class FakeSyncExchange:
    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        assert timeframe == "1m"
        assert params == {}
        return [
            [1767225600000, "100", "110", "90", "105", "12.5"],
            [1767225660000, "105", "111", "101", "106", "8"],
        ]

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        return {
            "id": "order-1",
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": str(amount),
            "price": str(price),
            "params": params,
        }

    def cancel_order(self, id, symbol=None, params=None):
        return {"id": id, "symbol": symbol, "status": "canceled", "params": params}

    def fetch_balance(self, params=None):
        return {"free": {"USDT": "90"}, "used": {"USDT": "10"}, "total": {"USDT": "100"}}

    def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        assert since is None
        assert limit == 10
        assert params == {"type": "spot"}
        return [
            {
                "id": "venue-order-1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "type": "limit",
                "amount": "1",
                "remaining": "0.75",
                "filled": "0.25",
                "price": "100",
                "cost": "75",
            }
        ]

    def close(self):
        pass


class FakeAsyncExchange:
    async def fetch_ticker(self, symbol):
        assert symbol == "BTC/USDT"
        return {
            "timestamp": 1767225600000,
            "bid": "100",
            "ask": "101",
            "last": "100.5",
            "baseVolume": "10",
            "quoteVolume": "1000",
        }

    async def fetch_order_book(self, symbol, limit=None):
        assert symbol == "BTC/USDT"
        assert limit == 5
        return {
            "timestamp": 1767225600000,
            "bids": [["100", "1.2"]],
            "asks": [["101", "0.8"]],
            "nonce": 7,
        }

    async def fetch_trades(self, symbol, since=None, limit=None):
        assert symbol == "BTC/USDT"
        return [
            {"timestamp": 1767225600000, "id": "1", "side": "buy", "price": "100", "amount": "0.1", "cost": "10"},
        ]

    async def close(self):
        pass


class FakeWsAsyncExchange:
    async def watch_ticker(self, symbol, params=None):
        assert params == {"type": "spot"}
        return {"timestamp": 1767225600000, "bid": "99", "ask": "101", "last": "100"}

    async def watch_order_book(self, symbol, limit=None, params=None):
        assert limit == 5
        assert params == {"type": "spot"}
        return {"timestamp": 1767225600000, "bids": [["100", "1"]], "asks": [["101", "2"]]}

    async def watch_trades(self, symbol, since=None, limit=None, params=None):
        assert since is None
        assert limit == 50
        assert params == {"type": "spot"}
        return [{"timestamp": 1767225600000, "id": "ws-1", "price": "100", "amount": "1"}]

    async def watch_balance(self, params=None):
        assert params == {"type": "spot"}
        return {"free": {"USDT": "90"}, "used": {"USDT": "10"}, "total": {"USDT": "100"}}

    async def watch_orders(self, symbol=None, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        assert since is None
        assert limit == 10
        assert params == {"type": "spot"}
        return [{"id": "ws-order-1", "symbol": symbol, "side": "buy", "amount": "1"}]

    async def watch_my_trades(self, symbol=None, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        assert since is None
        assert limit == 10
        assert params == {"type": "spot"}
        return [{"id": "ws-fill-1", "order": "ws-order-1", "symbol": symbol, "amount": "1"}]

    async def fetch_ticker(self, symbol):
        raise AssertionError("REST ticker fallback should not be used when watch_ticker exists")

    async def fetch_order_book(self, symbol, limit=None):
        raise AssertionError("REST order book fallback should not be used when watch_order_book exists")

    async def fetch_trades(self, symbol, since=None, limit=None):
        raise AssertionError("REST trades fallback should not be used when watch_trades exists")

    async def close(self):
        pass


def _driver() -> CcxtDriver:
    return CcxtDriver(
        exchange_factory=lambda exchange_id: FakeSyncExchange(),
        async_exchange_factory=lambda exchange_id: FakeAsyncExchange(),
    )


def _ws_driver() -> CcxtDriver:
    return CcxtDriver(
        exchange_factory=lambda exchange_id: FakeSyncExchange(),
        async_exchange_factory=lambda exchange_id: FakeWsAsyncExchange(),
    )


def test_binance_exchange_uses_ccxt_driver_for_historical_ohlcv_download() -> None:
    rows = list(BinanceMarketDataConnector(_driver()).fetch_ohlcv("BTC/USDT", timeframe="1m", limit=100))

    assert rows[0] == {
        "time": "2026-01-01T00:00:00+00:00",
        "kind": "ohlcv",
        "market_id": "market:binance:spot:btc_usdt",
        "instrument_id": "instrument:spot:btc:usdt",
        "venue": "binance",
        "market": "spot",
        "market_key": "binance_spot_btc_usdt",
        "source_symbol": "BTC/USDT",
        "timeframe": "1m",
        "open": "100",
        "high": "110",
        "low": "90",
        "close": "105",
        "volume": "12.5",
    }
    assert rows[1]["time"] == "2026-01-01T00:01:00+00:00"
    assert rows[1]["close"] == "106"


def test_historical_rows_are_persisted_by_data_store_not_exchange_or_driver() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")

        rows = BinanceMarketDataConnector(_driver()).fetch_ohlcv("BTC/USDT")
        store.write("market.ohlcv.btc_usdt.1m", rows)

        assert [row["close"] for row in store.read_rows("market.ohlcv.btc_usdt.1m")] == ["105", "106"]


def test_binance_exchange_exposes_historical_ohlcv_updates() -> None:
    updates = list(BinanceMarketDataConnector(_driver()).fetch_ohlcv_updates("BTC/USDT", timeframe="1m", limit=100))

    assert all(isinstance(update, MarketEvent) for update in updates)
    assert updates[0].kind == "bar"
    assert isinstance(updates[0].value, Bar)
    assert updates[0].value.market_id == "market:binance:spot:btc_usdt"
    assert updates[0].value.close.normalize() == 105
    assert updates[1].value.close.normalize() == 106


def test_binance_exchange_uses_ccxt_driver_for_live_ticker_stream() -> None:
    async def scenario() -> None:
        events = [
            event
            async for event in BinanceMarketDataConnector(_driver()).watch_ticker(
                "BTC/USDT",
                params={"max_events": 1, "poll_seconds": 0},
            )
        ]

        assert events == [{
            "time": "2026-01-01T00:00:00+00:00",
            "kind": "ticker",
            "market_id": "market:binance:spot:btc_usdt",
            "instrument_id": "instrument:spot:btc:usdt",
            "venue": "binance",
            "market": "spot",
            "market_key": "binance_spot_btc_usdt",
            "source_symbol": "BTC/USDT",
            "bid1": "100",
            "ask1": "101",
            "last": "100.5",
            "base_volume": "10",
            "quote_volume": "1000",
        }]

    asyncio.run(scenario())


def test_binance_exchange_exposes_live_ticker_updates() -> None:
    async def scenario() -> None:
        updates = [
            update
            async for update in BinanceMarketDataConnector(_driver()).watch_ticker_updates(
                "BTC/USDT",
                params={"max_events": 1, "poll_seconds": 0},
            )
        ]

        assert len(updates) == 1
        assert isinstance(updates[0], MarketEvent)
        assert updates[0].kind == "quote"
        assert isinstance(updates[0].value, Quote)
        assert updates[0].value.market_key == "binance_spot_btc_usdt"
        assert updates[0].value.bid.normalize() == 100
        assert updates[0].value.ask.normalize() == 101

    asyncio.run(scenario())


def test_ccxt_market_payload_adapter_emits_core_market_event() -> None:
    update = ccxt_ticker_update(
        {"timestamp": 1767225600000, "bid": "100", "ask": "101"},
        market=ephemeral_market_ref(venue="binance", market="spot", source_symbol="BTC/USDT"),
    )

    assert isinstance(update, MarketEvent)
    assert update.subject.subject_type == "instrument"
    assert update.subject.subject_id == "instrument:spot:btc:usdt"
    assert update.kind == "quote"
    assert isinstance(update.value, Quote)
    assert update.value.market_id == "market:binance:spot:btc_usdt"
    assert update.value.bid.normalize() == 100
    assert update.value.ask.normalize() == 101


def test_live_orderbook_can_be_persisted_to_data_store() -> None:
    async def scenario() -> None:
        with TemporaryDirectory() as temporary:
            store = DataStore(temporary, storage_format="jsonl")
            sink = DataSink(store, "market.orderbook.btc_usdt")

            count = await BinanceMarketDataConnector(_driver()).persist_order_book(
                "BTC/USDT",
                sink,
                book_limit=5,
                limit=1,
                params={"max_events": 1, "poll_seconds": 0},
            )

            rows = store.read_rows("market.orderbook.btc_usdt")
            assert count == 1
            assert rows[0]["kind"] == "orderbook"
            assert rows[0]["market_key"] == "binance_spot_btc_usdt"
            assert rows[0]["bid1"] == "100"
            assert rows[0]["bid1_size"] == "1.2"
            assert rows[0]["ask1"] == "101"
            assert rows[0]["ask1_size"] == "0.8"
            assert rows[0]["bid_depth"] == 1
            assert rows[0]["ask_depth"] == 1
            assert rows[0]["bids"] == [["100", "1.2"]]
            assert rows[0]["asks"] == [["101", "0.8"]]

    asyncio.run(scenario())


def test_binance_exchange_uses_ccxt_driver_for_live_trades_stream() -> None:
    async def scenario() -> None:
        events = [
            event
            async for event in BinanceMarketDataConnector(_driver()).watch_trades(
                "BTC/USDT",
                params={"max_events": 1, "poll_seconds": 0},
            )
        ]

        assert events == [{
            "time": "2026-01-01T00:00:00+00:00",
            "kind": "trade",
            "market_id": "market:binance:spot:btc_usdt",
            "instrument_id": "instrument:spot:btc:usdt",
            "venue": "binance",
            "market": "spot",
            "market_key": "binance_spot_btc_usdt",
            "source_symbol": "BTC/USDT",
            "id": "1",
            "side": "buy",
            "price": "100",
            "size": "0.1",
            "amount": "0.1",
            "cost": "10",
        }]

    asyncio.run(scenario())


def test_binance_exchange_prefers_ccxt_pro_watch_methods_for_live_streams() -> None:
    async def scenario() -> None:
        exchange = BinanceMarketDataConnector(_ws_driver())

        ticker = [
            event
            async for event in exchange.watch_ticker(
                "BTC/USDT",
                params={"type": "spot", "max_events": 1, "poll_seconds": 0},
            )
        ]
        book = [
            event
            async for event in exchange.watch_order_book(
                "BTC/USDT",
                limit=5,
                params={"type": "spot", "max_events": 1, "poll_seconds": 0},
            )
        ]
        trades = [
            event
            async for event in exchange.watch_trades(
                "BTC/USDT",
                params={"type": "spot", "max_events": 1, "poll_seconds": 0},
            )
        ]

        assert ticker[0]["last"] == "100"
        assert book[0]["bids"] == [["100", "1"]]
        assert trades[0]["id"] == "ws-1"

    asyncio.run(scenario())


def test_ccxt_driver_can_require_websocket_live_streams() -> None:
    async def scenario() -> None:
        driver = CcxtDriver(async_exchange_factory=lambda exchange_id: FakeAsyncExchange(), require_websocket=True)
        try:
            _ = [
                event
                async for event in BinanceMarketDataConnector(driver).watch_ticker(
                    "BTC/USDT",
                    params={"max_events": 1, "poll_seconds": 0},
                )
            ]
        except Exception as error:
            assert error.__class__.__name__ == "_WsUnavailable"
        else:
            raise AssertionError("required websocket stream should not fall back to REST polling")

    asyncio.run(scenario())


def test_binance_broker_uses_same_ccxt_driver_for_trading_and_account() -> None:
    broker = BinanceBroker(_driver())

    order = broker.create_order("BTC/USDT", side="buy", type="limit", amount="0.1", price="100")
    open_orders = tuple(broker.fetch_open_orders("BTC/USDT", limit=10, params={"type": "spot"}))

    assert order["id"] == "order-1"
    assert broker.cancel_order("order-1", symbol="BTC/USDT")["status"] == "canceled"
    assert broker.fetch_balance()["free"]["USDT"] == "90"
    assert open_orders[0]["id"] == "venue-order-1"


def test_binance_broker_exposes_ccxt_pro_account_watch_methods() -> None:
    async def scenario() -> None:
        broker = BinanceBroker(_ws_driver())

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
        assert orders[0]["id"] == "ws-order-1"
        assert fills[0]["id"] == "ws-fill-1"

    asyncio.run(scenario())
