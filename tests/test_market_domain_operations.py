from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping

import pytest

from kairospy.application.service.domain.market import MarketDataOperationsService, MarketDataSpec
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
    assert resolved.dataset_id == "market.ohlcv.binance_spot_btc_usdt.1m"
    assert rows[0]["close"] == "100.5"


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
