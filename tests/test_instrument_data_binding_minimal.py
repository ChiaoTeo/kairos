from __future__ import annotations

from tempfile import TemporaryDirectory

from kairospy.context import DataContext
from kairospy.data import DataStore
from kairospy.reference import MarketResolver


def test_data_context_resolves_named_market_to_dataset_view() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.binance_spot_btc_usdt.1m", [
            {"time": "2026-01-01T00:00:00+00:00", "close": 100},
        ])
        data = DataContext(
            store,
            markets=MarketResolver(default_venue="binance", default_market="spot"),
        )

        bars = data.for_market("BTC/USDT").ohlcv("1m")

        assert bars.binding.dataset == "market.ohlcv.binance_spot_btc_usdt.1m"
        assert bars.latest() == {"time": "2026-01-01T00:00:00+00:00", "close": 100}
        assert data.snapshot()["markets"]["binance_spot_btc_usdt"]["source_symbol"] == "BTC/USDT"


def test_market_reference_can_include_venue_and_market() -> None:
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))

    view = data.for_market("hyperliquid:perp:BTC/USDC:USDC").orderbook()

    assert view.binding.stream == "market.orderbook.hyperliquid_perp_btc_usdc_usdc"
    assert view.binding.mode == "stream"
