from __future__ import annotations

from tempfile import TemporaryDirectory

from kairospy.application.context import DataContext
from kairospy.infrastructure.data import DataStore
from kairospy.core.reference import MarketResolver
from kairospy.application.service.domains.market import bind_market_data, market_data_id_from_symbol


def test_data_context_resolves_named_market_to_dataset_view() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.binance_spot_btc_usdt.1m", [
            {"time": "2026-01-01T00:00:00+00:00", "close": 100},
        ])
        resolver = MarketResolver(default_venue="binance", default_market="spot")
        data = DataContext(store)

        bars = bind_market_data(data, resolver, "BTC/USDT").ohlcv("1m")

        assert bars.binding.dataset == "market.ohlcv.binance_spot_btc_usdt.1m"
        assert bars.latest() == {"time": "2026-01-01T00:00:00+00:00", "close": 100}
        assert resolver.snapshot()["binance_spot_btc_usdt"]["source_symbol"] == "BTC/USDT"


def test_market_reference_can_include_venue_and_market() -> None:
    resolver = MarketResolver()
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))

    view = bind_market_data(data, resolver, "hyperliquid:perp:BTC/USDC:USDC").orderbook()

    assert view.binding.stream == "market.orderbook.hyperliquid_perp_btc_usdc_usdc"
    assert view.binding.mode == "stream"


def test_market_data_id_uses_same_market_key_for_history_and_live_data() -> None:
    assert (
        market_data_id_from_symbol("ohlcv", "BTC/USDT", venue="binance", market="spot", timeframe="1m")
        == "market.ohlcv.binance_spot_btc_usdt.1m"
    )
    assert (
        market_data_id_from_symbol("orderbook", "BTC/USDT", venue="binance", market="spot")
        == "market.orderbook.binance_spot_btc_usdt"
    )
    assert (
        market_data_id_from_symbol("trade", "BTC/USDT", venue="binance", market="spot")
        == "market.trades.binance_spot_btc_usdt"
    )
