from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from tempfile import TemporaryDirectory

import pytest

from kairospy.data import DataStore
from kairospy.integrations import IntegrationRegistry, InstrumentReferenceSnapshotProvider, Massive, MassiveDriver
from kairospy.integrations.simulated import SimulatedProvider
from kairospy.data import DataSink


class FakeMassiveDriver:
    def fetch_ohlcv(self, symbol, *, timeframe="1m", since=None, until=None, limit=1000, params=None):
        return [{
            "time": "2026-01-01T00:00:00+00:00",
            "symbol": symbol,
            "timeframe": timeframe,
            "close": "100",
        }]

    async def watch_trades(self, symbol, *, since=None, limit=50, params=None):
        yield {"time": "2026-01-01T00:00:00+00:00", "symbol": symbol, "price": "100"}


class FakeInstrumentProvider:
    def fetch_markets(self, *, params=None):
        assert params == {"type": "spot"}
        return (
            {
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "active": True,
            },
        )


def test_registry_registers_and_resolves_integrations() -> None:
    simulated = SimulatedProvider()
    massive = Massive(FakeMassiveDriver())
    registry = IntegrationRegistry.with_items([simulated, massive])

    assert registry.names() == ("massive", "simulated")
    assert registry.get("massive") is massive
    assert registry.provider("simulated") is simulated


def test_simulated_provider_produces_rows_without_writing_data() -> None:
    provider = SimulatedProvider(rows=(
        {"time": "2026-01-01T00:00:00+00:00", "symbol": "BTCUSDT", "close": 100},
        {"time": "2026-01-01T00:01:00+00:00", "symbol": "ETHUSDT", "close": 200},
    ))

    rows = list(provider.fetch_ohlcv("BTCUSDT"))

    assert rows == [{"time": "2026-01-01T00:00:00+00:00", "symbol": "BTCUSDT", "close": 100}]


def test_instrument_provider_can_be_adapted_to_reference_snapshot() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snapshot = InstrumentReferenceSnapshotProvider(FakeInstrumentProvider()).reference_snapshot(
        as_of=as_of,
        params={"type": "spot"},
    )

    assert snapshot.resolve_market("BTC/USDT", venue="binance", market="spot", at=as_of).source_symbol == "BTC/USDT"


def test_historical_rows_are_persisted_by_data_store_not_integration() -> None:
    with TemporaryDirectory() as temporary:
        provider = SimulatedProvider(rows=({"time": "2026-01-01T00:00:00+00:00", "close": 100},))
        store = DataStore(temporary, storage_format="jsonl")

        store.write("market.ohlcv.btc_usdt.1m", provider.fetch_ohlcv("BTCUSDT"))

        assert store.read_rows("market.ohlcv.btc_usdt.1m")[0]["close"] == 100


def test_simulated_live_streams_events_to_data_sink() -> None:
    async def scenario() -> None:
        with TemporaryDirectory() as temporary:
            provider = SimulatedProvider(events=(
                {"time": "2026-01-01T00:00:00+00:00", "symbol": "BTCUSDT", "price": 100},
                {"time": "2026-01-01T00:00:01+00:00", "symbol": "ETHUSDT", "price": 200},
            ))
            store = DataStore(temporary, storage_format="jsonl")
            sink = DataSink(store, "market.trades.btc_usdt")

            count = await sink.consume(provider.watch_trades("BTCUSDT"))

            assert count == 1
            assert store.read_rows("market.trades.btc_usdt")[0]["price"] == 100

    asyncio.run(scenario())


def test_integration_output_without_time_is_rejected_by_data_store() -> None:
    with TemporaryDirectory() as temporary:
        provider = SimulatedProvider(rows=({"symbol": "BTCUSDT", "close": 100},))
        store = DataStore(temporary, storage_format="jsonl")

        with pytest.raises(ValueError, match="time field"):
            store.write("market.bad", provider.fetch_ohlcv("BTCUSDT"))


def test_massive_provider_delegates_to_massive_driver() -> None:
    provider = Massive(FakeMassiveDriver())

    assert list(provider.fetch_ohlcv("AAPL"))[0]["symbol"] == "AAPL"


def test_massive_driver_is_explicitly_unimplemented() -> None:
    with pytest.raises(NotImplementedError):
        list(MassiveDriver().fetch_ohlcv("AAPL"))


def test_massive_driver_fetches_equity_reference_markets_with_pagination() -> None:
    requested = []

    def http_get(url):
        requested.append(url)
        if "cursor=next" in url:
            return {
                "results": [
                    {
                        "ticker": "msft",
                        "name": "Microsoft Corporation",
                        "primary_exchange": "XNAS",
                        "currency_name": "usd",
                        "cik": "0000789019",
                        "active": True,
                    },
                ],
            }
        return {
            "results": [
                {
                    "ticker": "aapl",
                    "name": "Apple Inc.",
                    "primary_exchange": "XNAS",
                    "currency_name": "usd",
                    "cik": "0000320193",
                    "composite_figi": "BBG000B9XRY4",
                    "active": True,
                },
            ],
            "next_url": "/v3/reference/tickers?cursor=next",
        }

    rows = list(MassiveDriver(api_key="key", http_get=http_get).fetch_markets(params={
        "asset_class": "equity",
        "include_inactive": False,
        "limit": 2,
    }))

    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["venue"] == "XNAS"
    assert rows[0]["currency"] == "usd"
    assert rows[0]["venue_instrument_id"] == "BBG000B9XRY4"
    assert rows[1]["ticker"] == "MSFT"
    assert "/v3/reference/tickers" in requested[0]
    assert "market=stocks" in requested[0]
    assert "type=CS" in requested[0]
    assert "active=true" in requested[0]
    assert "apiKey=key" in requested[0]
    assert "cursor=next" in requested[1]


def test_massive_driver_fetches_corporate_action_reference_rows() -> None:
    requested: list[str] = []

    def http_get(url: str):
        requested.append(url)
        if "/stocks/v1/splits" in url:
            return {"results": [{"ticker": "AAPL", "execution_date": "2026-01-02", "split_from": 1, "split_to": 4}]}
        if "/stocks/v1/dividends" in url:
            return {"results": [{"ticker": "AAPL", "ex_dividend_date": "2026-02-02", "cash_amount": 0.26}]}
        if "/vX/reference/tickers/AAPL/events" in url:
            return {"results": {"events": [{"date": "2026-03-03", "ticker_change": {"ticker": "APPL"}, "type": "ticker_change"}]}}
        raise AssertionError(url)

    driver = MassiveDriver(api_key="key", http_get=http_get)
    start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    end = datetime.fromisoformat("2026-04-01T00:00:00+00:00")

    splits = list(driver.fetch_splits("aapl", start=start, end=end))
    dividends = list(driver.fetch_dividends("aapl", start=start, end=end))
    ticker_events = list(driver.fetch_ticker_events("aapl"))

    assert splits[0]["split_to"] == 4
    assert dividends[0]["cash_amount"] == 0.26
    assert ticker_events[0]["ticker_change"]["ticker"] == "APPL"
    assert "/stocks/v1/splits" in requested[0]
    assert "execution_date.gte=2026-01-01" in requested[0]
    assert "execution_date.lt=2026-04-01" in requested[0]
    assert "/stocks/v1/dividends" in requested[1]
    assert "ex_dividend_date.gte=2026-01-01" in requested[1]
    assert "types=ticker_change" in requested[2]
    assert "apiKey=key" in requested[2]
