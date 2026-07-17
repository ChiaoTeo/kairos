from __future__ import annotations

from dataclasses import replace

from .models import (
    DatasetKey, DatasetLayer, DatasetProduct, DatasetProductSpec, DatasetStorageKind, QualityLevel,
    SourceBinding,
)


# Compatibility name for pipeline code while DatasetProductSpec becomes the sole contract.
ManagedDataset = DatasetProductSpec


def _governed(product, description: str):
    owner = "research-platform" if product.layer.value in {"features", "studies"} else "data-platform"
    return replace(product, description=description, owner=owner)


def _product(key, title, layer, dimensions, *, primary_time="available_time", sources=()):
    return DatasetProduct(
        DatasetKey(key), title, layer, dimensions=dimensions, primary_time=primary_time, sources=sources,
    )


_BTC_SPOT_DAILY_PRODUCT = _product(
    "market.ohlcv.crypto.binance.btc-usdt.1d", "Binance BTC/USDT daily OHLCV", DatasetLayer.CANONICAL,
    {"asset_class": "crypto", "venue": "binance", "instrument": "BTC-USDT", "frequency": "1d"},
    primary_time="period_start",
    sources=(SourceBinding("binance", "binance", 100, QualityLevel.BACKTEST, ("public-archive",)),),
)
_BTC_DVOL_DAILY_PRODUCT = _product(
    "analytics.vendor_volatility_index.deribit.btc-dvol.1d", "Deribit BTC DVOL daily", DatasetLayer.CANONICAL,
    {"asset_class": "crypto", "venue": "deribit", "underlying": "BTC", "frequency": "1d"},
    primary_time="period_start",
    sources=(SourceBinding("deribit", "deribit", 100, QualityLevel.RESEARCH, ("public-api",)),),
)
_BTC_OPTION_QUOTES_HOURLY_PRODUCT = _product(
    "derivatives.option_quotes.crypto.binance.btc-usdt.1h", "Binance BTC option quotes hourly",
    DatasetLayer.CANONICAL,
    {"asset_class": "option", "venue": "binance", "underlying": "BTC-USDT", "frequency": "1h"},
    primary_time="period_start",
    sources=(SourceBinding("binance", "binance", 100, QualityLevel.RESEARCH, ("public-archive",)),),
)
_BTC_DERIBIT_OPTION_TRADES_PRODUCT = _product(
    "derivatives.option_trades.crypto.deribit.btc", "Deribit BTC option trades", DatasetLayer.CANONICAL,
    {"asset_class": "option", "venue": "deribit", "underlying": "BTC", "frequency": "event"},
    sources=(SourceBinding("deribit", "deribit", 100, QualityLevel.RESEARCH, ("public-api",)),),
)
_BTC_DERIBIT_OPTION_QUOTES_PRODUCT = _product(
    "derivatives.option_quotes.crypto.deribit.btc.snapshots", "Deribit BTC option snapshots",
    DatasetLayer.CANONICAL,
    {"asset_class": "option", "venue": "deribit", "underlying": "BTC", "frequency": "snapshot"},
    sources=(SourceBinding("deribit", "deribit", 100, QualityLevel.RESEARCH, ("public-api",)),),
)
_BTC_IV_RV_DAILY_PRODUCT = _product(
    "features.volatility.btc.iv-rv.1d", "BTC IV/RV daily features", DatasetLayer.FEATURES,
    {"asset_class": "crypto", "underlying": "BTC", "frequency": "1d"}, primary_time="period_start",
)
_BTC_TERM_SKEW_HOURLY_PRODUCT = _product(
    "features.volatility_surface.btc.term-skew.1h", "BTC term skew hourly features", DatasetLayer.FEATURES,
    {"asset_class": "option", "underlying": "BTC", "frequency": "1h"}, primary_time="period_start",
)
_BTC_DERIBIT_TERM_SKEW_DAILY_PRODUCT = _product(
    "features.volatility_surface.btc.deribit-trade-term-skew.1d", "Deribit BTC term skew daily",
    DatasetLayer.FEATURES,
    {"asset_class": "option", "venue": "deribit", "underlying": "BTC", "frequency": "1d"},
    primary_time="period_start",
)


def _capabilities(*, point_in_time_universe=False, synchronous_quotes=False, top_of_book=False,
                  trade_events=False, trade_direction=False, products=(), maximum_validation_level=2):
    return {
        "point_in_time_universe": point_in_time_universe, "synchronous_quotes": synchronous_quotes,
        "top_of_book": top_of_book, "quote_size": False, "order_book_depth": False,
        "trade_events": trade_events, "trade_direction": trade_direction,
        "queue_reconstructable": False, "settlement_price": False, "lifecycle_events": False,
        "supported_products": list(products), "supported_return_drivers": [],
        "maximum_validation_level": maximum_validation_level,
    }


BTC_SPOT_DAILY = ManagedDataset(
    _governed(_BTC_SPOT_DAILY_PRODUCT, "Point-in-time Binance BTC/USDT daily OHLCV bars."),
    "canonical/market/ohlcv/asset_class=crypto/venue=binance/instrument=BTC-USDT/interval=1d",
    "market.ohlcv.v1", _capabilities(point_in_time_universe=True, products=("spot",)),
    quality_profile="ohlcv", minimum_publication_level=QualityLevel.BACKTEST,
)
BTC_DVOL_DAILY = ManagedDataset(
    _governed(_BTC_DVOL_DAILY_PRODUCT, "Deribit BTC DVOL observations for volatility research."),
    "canonical/analytics/vendor_volatility_indices/provider=deribit/underlying=BTC/index=DVOL/interval=1d",
    "analytics.vendor_volatility_index.v1", _capabilities(), quality_profile="generic",
)
BTC_IV_RV_DAILY = ManagedDataset(
    _governed(_BTC_IV_RV_DAILY_PRODUCT, "Daily BTC implied-versus-realized volatility features."),
    "features/volatility/underlying=BTC/frequency=1d/feature_set=iv_rv_v1",
    "features.volatility.iv_rv_daily.v1", _capabilities(), quality_profile="feature",
)
BTC_OPTION_QUOTES_HOURLY = ManagedDataset(
    _governed(_BTC_OPTION_QUOTES_HOURLY_PRODUCT, "Hourly Binance BTC option quote snapshots."),
    "canonical/derivatives/option_quotes/asset_class=crypto/venue=binance/underlying=BTC-USDT/interval=1h",
    "derivatives.option_quote_summary.v1",
    _capabilities(point_in_time_universe=True, synchronous_quotes=True, top_of_book=True, products=("option",), maximum_validation_level=3),
    quality_profile="option_snapshot",
)
BTC_TERM_SKEW_HOURLY = ManagedDataset(
    _governed(_BTC_TERM_SKEW_HOURLY_PRODUCT, "Hourly BTC volatility term-skew features."),
    "features/volatility_surface/underlying=BTC/frequency=1h/feature_set=term_skew_v1",
    "features.volatility_surface.term_skew.v1", _capabilities(point_in_time_universe=True, products=("option",)),
    quality_profile="feature",
)
BTC_DERIBIT_OPTION_TRADES = ManagedDataset(
    _governed(_BTC_DERIBIT_OPTION_TRADES_PRODUCT, "Canonical Deribit BTC option trade events."),
    "canonical/derivatives/option_trades/asset_class=crypto/venue=deribit/underlying=BTC",
    "derivatives.option_trade.v1",
    _capabilities(point_in_time_universe=True, trade_events=True, trade_direction=True, products=("option",), maximum_validation_level=3),
    quality_profile="trade",
)
BTC_DERIBIT_TERM_SKEW_DAILY = ManagedDataset(
    _governed(_BTC_DERIBIT_TERM_SKEW_DAILY_PRODUCT, "Daily BTC term-skew features derived from Deribit trades."),
    "features/volatility_surface/underlying=BTC/frequency=1d/feature_set=deribit_trade_term_skew_v1",
    "features.volatility_surface.trade_term_skew.v1", _capabilities(point_in_time_universe=True, products=("option",)),
    quality_profile="feature",
)
BTC_DERIBIT_OPTION_QUOTES = ManagedDataset(
    _governed(_BTC_DERIBIT_OPTION_QUOTES_PRODUCT, "Point-in-time Deribit BTC option-chain snapshots."),
    "canonical/derivatives/option_quotes/asset_class=crypto/venue=deribit/underlying=BTC",
    "derivatives.option_chain_summary.v1",
    _capabilities(point_in_time_universe=True, synchronous_quotes=True, top_of_book=True, products=("option",), maximum_validation_level=3),
    quality_profile="option_snapshot",
)


def capabilities_payload(dataset: ManagedDataset, release_id: str) -> dict[str, object]:
    return {"capability_schema_version": 2, "dataset_id": release_id, **dict(dataset.capabilities)}


class Datasets:
    """Compatibility handles backed by the authoritative DatasetProductSpec objects."""

    MARKET_OHLCV_CRYPTO_BINANCE_BTC_USDT_1D = BTC_SPOT_DAILY.product
    ANALYTICS_VENDOR_VOLATILITY_INDEX_DERIBIT_BTC_DVOL_1D = BTC_DVOL_DAILY.product
    DERIVATIVES_OPTION_QUOTES_CRYPTO_BINANCE_BTC_USDT_1H = BTC_OPTION_QUOTES_HOURLY.product
    DERIVATIVES_OPTION_TRADES_CRYPTO_DERIBIT_BTC = BTC_DERIBIT_OPTION_TRADES.product
    DERIVATIVES_OPTION_QUOTES_CRYPTO_DERIBIT_BTC_SNAPSHOTS = BTC_DERIBIT_OPTION_QUOTES.product
    FEATURES_VOLATILITY_BTC_IV_RV_1D = BTC_IV_RV_DAILY.product
    FEATURES_VOLATILITY_SURFACE_BTC_TERM_SKEW_1H = BTC_TERM_SKEW_HOURLY.product
    FEATURES_VOLATILITY_SURFACE_BTC_DERIBIT_TRADE_TERM_SKEW_1D = BTC_DERIBIT_TERM_SKEW_DAILY.product
    MARKET_EVENTS_OPTIONS_US_SPXW = DatasetProduct(
        DatasetKey("market.events.options.us.spxw"), "US SPXW option market events", DatasetLayer.CANONICAL,
        dimensions={"asset_class": "option", "region": "us", "underlying": "SPX",
                    "contract_family": "SPXW", "frequency": "event"},
        sources=(SourceBinding("massive", "opra", 100, QualityLevel.BACKTEST, ("rest", "flat-file")),),
    )
