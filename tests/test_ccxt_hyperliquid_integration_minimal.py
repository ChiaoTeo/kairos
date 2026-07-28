from __future__ import annotations

import asyncio

from kairospy.infrastructure.integrations import CcxtDriver, HyperliquidMarketDataConnector
from kairospy.core.market import MarketEvent, Quote
from kairospy.core.reference import MarketRef
from kairospy.surface.runtime import DriverName, ExchangeName, exchange


class FakeSyncExchange:
    def fetch_ticker(self, symbol, params=None):
        assert symbol == "BTC/USDC:USDC"
        assert params == {}
        return {
            "timestamp": 1767225600000,
            "bid": "100",
            "ask": "101",
            "last": "100.5",
            "baseVolume": "10",
            "quoteVolume": "1000",
        }

    def close(self):
        pass


class FakeAsyncExchange:
    async def fetch_ticker(self, symbol):
        assert symbol == "BTC/USDC:USDC"
        return {
            "timestamp": 1767225600000,
            "bid": "100",
            "ask": "101",
            "last": "100.5",
            "baseVolume": "10",
            "quoteVolume": "1000",
        }

    async def close(self):
        pass


def test_hyperliquid_exchange_uses_ccxt_driver_and_normalizes_bare_symbol() -> None:
    seen_exchange_ids: list[str] = []

    def async_exchange_factory(exchange_id: str) -> FakeAsyncExchange:
        seen_exchange_ids.append(exchange_id)
        return FakeAsyncExchange()

    async def scenario() -> None:
        events = [
            event
            async for event in HyperliquidMarketDataConnector(
                CcxtDriver(async_exchange_factory=async_exchange_factory),
            ).watch_ticker(
                "BTC",
                params={"max_events": 1, "poll_seconds": 0},
            )
        ]

        assert events == [{
            "time": "2026-01-01T00:00:00+00:00",
            "kind": "ticker",
            "market_id": "market:hyperliquid:derivative:btc_usdc_usdc",
            "instrument_id": "instrument:derivative:btc:usdc_usdc",
            "venue": "hyperliquid",
            "market": "derivative",
            "market_key": "hyperliquid_derivative_btc_usdc_usdc",
            "source_symbol": "BTC/USDC:USDC",
            "bid1": "100",
            "ask1": "101",
            "last": "100.5",
            "base_volume": "10",
            "quote_volume": "1000",
        }]

    asyncio.run(scenario())

    assert seen_exchange_ids == ["hyperliquid"]


def test_hyperliquid_exchange_exposes_ticker_updates_with_normalized_symbol() -> None:
    seen_exchange_ids: list[str] = []

    def async_exchange_factory(exchange_id: str) -> FakeAsyncExchange:
        seen_exchange_ids.append(exchange_id)
        return FakeAsyncExchange()

    async def scenario() -> None:
        updates = [
            update
            async for update in HyperliquidMarketDataConnector(
                CcxtDriver(async_exchange_factory=async_exchange_factory),
            ).watch_ticker_updates(
                "BTC",
                params={"max_events": 1, "poll_seconds": 0},
            )
        ]

        assert len(updates) == 1
        assert isinstance(updates[0], MarketEvent)
        assert isinstance(updates[0].value, Quote)
        assert updates[0].value.market_id == "market:hyperliquid:derivative:btc_usdc_usdc"
        assert updates[0].value.bid.normalize() == 100
        assert updates[0].value.ask.normalize() == 101

    asyncio.run(scenario())

    assert seen_exchange_ids == ["hyperliquid"]


def test_hyperliquid_fetch_quote_uses_ccxt_ticker_for_clock_requests() -> None:
    seen_exchange_ids: list[str] = []

    def exchange_factory(exchange_id: str) -> FakeSyncExchange:
        seen_exchange_ids.append(exchange_id)
        return FakeSyncExchange()

    market = MarketRef.ephemeral(venue="hyperliquid", market="derivative", source_symbol="BTC")
    row = HyperliquidMarketDataConnector(CcxtDriver(exchange_factory=exchange_factory)).fetch_quote(market)

    assert seen_exchange_ids == ["hyperliquid"]
    assert row == {
        "time": "2026-01-01T00:00:00+00:00",
        "kind": "ticker",
        "market_id": "market:hyperliquid:derivative:btc_usdc_usdc",
        "instrument_id": "instrument:derivative:btc:usdc_usdc",
        "venue": "hyperliquid",
        "market": "derivative",
        "market_key": "hyperliquid_derivative_btc_usdc_usdc",
        "source_symbol": "BTC/USDC:USDC",
        "bid1": "100",
        "ask1": "101",
        "last": "100.5",
        "base_volume": "10",
        "quote_volume": "1000",
    }


def test_hyperliquid_fetch_quote_update_uses_core_market_event() -> None:
    seen_exchange_ids: list[str] = []

    def exchange_factory(exchange_id: str) -> FakeSyncExchange:
        seen_exchange_ids.append(exchange_id)
        return FakeSyncExchange()

    market = MarketRef.ephemeral(venue="hyperliquid", market="derivative", source_symbol="BTC")
    update = HyperliquidMarketDataConnector(CcxtDriver(exchange_factory=exchange_factory)).fetch_quote_update(market)

    assert seen_exchange_ids == ["hyperliquid"]
    assert isinstance(update, MarketEvent)
    assert isinstance(update.value, Quote)
    assert update.value.market_key == "hyperliquid_derivative_btc_usdc_usdc"
    assert update.value.bid.normalize() == 100
    assert update.value.ask.normalize() == 101


def test_runtime_constructs_hyperliquid_ccxt_exchange() -> None:
    integration = exchange(ExchangeName.hyperliquid, DriverName.ccxt)

    assert isinstance(integration, HyperliquidMarketDataConnector)
    assert integration.exchange_id == "hyperliquid"
