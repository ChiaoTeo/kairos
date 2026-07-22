from __future__ import annotations

from ...contracts import DataProductContract, DatasetLayer, QualityLevel, SourceBinding
from .._helpers import _capabilities, _governed, _product


_BTC_SPOT_DAILY_PRODUCT = _product(
    "market.ohlcv.crypto.binance.btc-usdt.1d",
    "Binance BTC/USDT daily OHLCV",
    DatasetLayer.CANONICAL,
    {"asset_class": "crypto", "venue": "binance", "instrument": "BTC-USDT", "frequency": "1d"},
    primary_time="period_start",
    sources=(SourceBinding("binance", "binance", 100, QualityLevel.BACKTEST, ("public-archive",)),),
)
_BINANCE_USDM_PERPETUAL_HOURLY_PRODUCT = _product(
    "market.ohlcv.crypto.binance.usdm-perpetual.1h",
    "Binance USD-M perpetual full-market hourly OHLCV",
    DatasetLayer.CANONICAL,
    {
        "asset_class": "crypto",
        "venue": "binance",
        "product": "perpetual",
        "margin_asset": "USDT",
        "universe": "full-market",
        "frequency": "1h",
    },
    primary_time="available_time",
    sources=(SourceBinding("binance", "binance", 100, QualityLevel.BACKTEST, ("public-archive",)),),
)
_BTC_OPTION_QUOTES_HOURLY_PRODUCT = _product(
    "derivatives.option_quotes.crypto.binance.btc-usdt.1h",
    "Binance BTC option quotes hourly",
    DatasetLayer.CANONICAL,
    {"asset_class": "option", "venue": "binance", "underlying": "BTC-USDT", "frequency": "1h"},
    primary_time="period_start",
    sources=(SourceBinding("binance", "binance", 100, QualityLevel.WORKSPACE, ("public-archive",)),),
)


BTC_SPOT_DAILY = DataProductContract(
    _governed(_BTC_SPOT_DAILY_PRODUCT, "Point-in-time Binance BTC/USDT daily OHLCV bars."),
    "canonical/market/ohlcv/asset_class=crypto/venue=binance/instrument=BTC-USDT/interval=1d",
    "market.ohlcv.v1",
    _capabilities(point_in_time_universe=True, products=("spot",)),
    quality_profile="ohlcv",
    minimum_publication_level=QualityLevel.BACKTEST,
)
BINANCE_USDM_PERPETUAL_HOURLY = DataProductContract(
    _governed(
        _BINANCE_USDM_PERPETUAL_HOURLY_PRODUCT,
        "Point-in-time Binance USD-M USDT perpetual full-market hourly OHLCV bars.",
    ),
    "canonical/market/ohlcv/asset_class=crypto/venue=binance/product=usdm-perpetual/interval=1h",
    "market.ohlcv.v1",
    _capabilities(point_in_time_universe=True, products=("perpetual",), maximum_validation_level=3),
    quality_profile="ohlcv",
    minimum_publication_level=QualityLevel.BACKTEST,
)
BTC_OPTION_QUOTES_HOURLY = DataProductContract(
    _governed(_BTC_OPTION_QUOTES_HOURLY_PRODUCT, "Hourly Binance BTC option quote snapshots."),
    "canonical/derivatives/option_quotes/asset_class=crypto/venue=binance/underlying=BTC-USDT/interval=1h",
    "derivatives.option_quote_summary.v1",
    _capabilities(
        point_in_time_universe=True,
        synchronous_quotes=True,
        top_of_book=True,
        products=("option",),
        maximum_validation_level=3,
    ),
    quality_profile="option_snapshot",
)


def spot_daily_builder(root):
    from kairospy.integrations.connectors.binance.datasets import BinanceSpotDatasetConnector

    return BinanceSpotDatasetConnector(root)


def usdm_perpetual_hourly_builder(root, *, progress=None, stop_event=None):
    from kairospy.integrations.connectors.binance.datasets import BinanceUsdmPerpetualHourlyDatasetConnector
    from kairospy.integrations.connectors.binance.historical_archive import BinanceUsdmPerpetualHourlyArchiveProvider

    archive = BinanceUsdmPerpetualHourlyArchiveProvider(progress=progress, stop_event=stop_event)
    return BinanceUsdmPerpetualHourlyDatasetConnector(root, archive)


def option_quotes_hourly_builder(root):
    from kairospy.integrations.connectors.binance.datasets import BinanceOptionQuotesDatasetConnector

    return BinanceOptionQuotesDatasetConnector(root)


def register(registry, root, *, progress=None, stop_event=None) -> None:
    registry.register(spot_daily_builder(root), (BTC_SPOT_DAILY,))
    registry.register(
        usdm_perpetual_hourly_builder(root, progress=progress, stop_event=stop_event),
        (BINANCE_USDM_PERPETUAL_HOURLY,),
    )
    registry.register(option_quotes_hourly_builder(root), (BTC_OPTION_QUOTES_HOURLY,))
