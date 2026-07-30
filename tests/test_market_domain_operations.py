from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from decimal import Decimal

import pytest

from kairospy.application.runtime.dispatch.context import RuntimeContext
from kairospy.application.runtime.ports import DataSubscription, MarketDataSubscriptionSpec
from kairospy.application.service.domain.market import IterableMarketEventSource, MarketDataOperationsService, MarketDataSpec
from kairospy.core.market import OptionGreeks
from kairospy.infrastructure.data import DataStore


class FakeHistoricalClient:
    def fetch_ohlcv(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        return (
            {
                "time": "2026-01-01T00:00:00+00:00",
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100.5",
                "volume": "10",
                "symbol": symbol,
                "timeframe": timeframe,
            },
        )


def test_market_data_operations_download_read_and_ensure(tmp_path) -> None:
    service = MarketDataOperationsService(DataStore(tmp_path, storage_format="jsonl"))
    spec = MarketDataSpec("BTC/USDT", "ohlcv", venue="binance", market="spot", timeframe="1m")

    path = service.download(spec, FakeHistoricalClient(), mode="replace")
    resolved = service.ensure(spec)
    rows = service.read(spec)

    assert path.exists()
    assert resolved.dataset_id == "market.ohlcv.binance.spot.btc_usdt.1m"
    assert rows[0]["close"] == "100.5"
    assert path == tmp_path / "datasets" / "market" / "ohlcv" / "binance" / "spot" / "btc_usdt" / "1m" / "date=2026-01-01" / "data.jsonl"


def test_market_data_operations_partitions_hourly_bars_by_month(tmp_path) -> None:
    service = MarketDataOperationsService(DataStore(tmp_path, storage_format="jsonl"))
    spec = MarketDataSpec("BTC/USDT", "ohlcv", venue="binance", market="spot", timeframe="1h")

    path = service.download(spec, FakeHistoricalClient(), mode="replace")

    assert path == tmp_path / "datasets" / "market" / "ohlcv" / "binance" / "spot" / "btc_usdt" / "1h" / "month=2026-01" / "data.jsonl"
    assert service.read(spec)[0]["timeframe"] == "1h"


def test_market_data_operations_rejects_noncanonical_dataset_id(tmp_path) -> None:
    service = MarketDataOperationsService(DataStore(tmp_path, storage_format="jsonl"))
    spec = MarketDataSpec("BTC/USDT", "ohlcv", dataset="market.ohlcv.binance_spot_btc_usdt.1m")

    with pytest.raises(ValueError, match="canonical"):
        service.read(spec)


def test_market_data_operations_requires_client_for_empty_dataset(tmp_path) -> None:
    service = MarketDataOperationsService(DataStore(tmp_path, storage_format="jsonl"))
    spec = MarketDataSpec("ETH/USDT", "ohlcv", venue="binance", market="spot", timeframe="1m")

    with pytest.raises(RuntimeError, match="no client"):
        service.ensure(spec)


def test_market_data_operations_persists_stream_rows(tmp_path) -> None:
    service = MarketDataOperationsService(DataStore(tmp_path, storage_format="jsonl"))
    spec = MarketDataSpec("ETH/USDT", "trades", venue="binance", market="spot")

    async def rows():
        yield {"time": "2026-01-01T00:00:00+00:00", "price": "100", "amount": "1"}

    count = asyncio.run(service.persist(spec, rows()))

    assert count == 1
    assert service.read(spec)[0]["price"] == "100"


def test_option_greeks_rows_replay_as_typed_market_events() -> None:
    source = IterableMarketEventSource(
        "binance-option",
        (
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "option_greeks",
                "instrument_id": "instrument:option:btc:260926_120000_c",
                "market_id": "market:binance:option:btc_260926_120000_c",
                "market_key": "binance_option_btc_260926_120000_c",
                "delta": "0.5",
                "gamma": "0.001",
                "implied_volatility": "0.6",
                "mark_price": "1234",
            },
        ),
    )

    event = asyncio.run(_first(source.events()))

    assert event.kind == "option_greeks"
    assert isinstance(event.payload.value, OptionGreeks)
    assert event.payload.value.implied_volatility == Decimal("0.6")


def test_runtime_context_subscribes_by_market_dataset_id() -> None:
    port = RecordingMarketDataPort()
    context = RuntimeContext(strategy_id="strategy-a", data=port)

    subscription = context.subscribe("market.ohlcv.binance.spot.btc_usdt.1m")

    assert subscription.spec.dataset_id == "market.ohlcv.binance.spot.btc_usdt.1m"
    assert subscription.spec.market.market_key == "binance_spot_btc_usdt"
    assert subscription.spec.selectors[0].interval == "1m"
    assert subscription.key.startswith("data.market_ohlcv_binance_spot_btc_usdt_1m")


class RecordingMarketDataPort:
    def __init__(self) -> None:
        self.items: list[DataSubscription] = []

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        subscription = DataSubscription(spec.key, spec)
        self.items.append(subscription)
        return subscription

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        return None

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return tuple(self.items)


async def _first(events):
    async for event in events:
        return event
    raise AssertionError("no events")
