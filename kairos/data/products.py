from __future__ import annotations

from dataclasses import replace

from .contracts import (
    DatasetKey, DatasetLayer, DataProductDefinition, DataProductContract, DatasetStorageKind, QualityLevel,
    SourceBinding,
)


# Compatibility name for pipeline code while DataProductContract becomes the sole contract.
ManagedDataset = DataProductContract


def _governed(product, description: str):
    owner = "research-platform" if product.layer.value in {"features", "studies"} else "data-platform"
    return replace(product, description=description, owner=owner)


def _product(key, title, layer, dimensions, *, primary_time="available_time", sources=()):
    return DataProductDefinition(
        DatasetKey(key), title, layer, dimensions=dimensions, primary_time=primary_time, sources=sources,
    )


_BTC_SPOT_DAILY_PRODUCT = _product(
    "market.ohlcv.crypto.binance.btc-usdt.1d", "Binance BTC/USDT daily OHLCV", DatasetLayer.CANONICAL,
    {"asset_class": "crypto", "venue": "binance", "instrument": "BTC-USDT", "frequency": "1d"},
    primary_time="period_start",
    sources=(SourceBinding("binance", "binance", 100, QualityLevel.BACKTEST, ("public-archive",)),),
)
_BINANCE_USDM_PERPETUAL_HOURLY_PRODUCT = _product(
    "market.ohlcv.crypto.binance.usdm-perpetual.1h",
    "Binance USD-M perpetual full-market hourly OHLCV",
    DatasetLayer.CANONICAL,
    {"asset_class": "crypto", "venue": "binance", "product": "perpetual",
     "margin_asset": "USDT", "universe": "full-market", "frequency": "1h"},
    primary_time="available_time",
    sources=(SourceBinding("binance", "binance", 100, QualityLevel.BACKTEST, ("public-archive",)),),
)
_US_EQUITY_MASSIVE_RAW_DAILY_PRODUCT = _product(
    "market.ohlcv.equity.us.massive.1d.raw",
    "Massive US equity daily raw OHLCV",
    DatasetLayer.CANONICAL,
    {"asset_class": "equity", "region": "us", "provider": "massive", "frequency": "1d", "view": "raw"},
    primary_time="available_time",
    sources=(SourceBinding("massive", "us-securities", 100, QualityLevel.RESEARCH, ("rest", "flat-file")),),
)
_US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY_PRODUCT = _product(
    "market.ohlcv.equity.us.massive.1d.vendor_adjusted",
    "Massive US equity daily vendor-adjusted OHLCV",
    DatasetLayer.CANONICAL,
    {"asset_class": "equity", "region": "us", "provider": "massive", "frequency": "1d", "view": "vendor-adjusted"},
    primary_time="available_time",
    sources=(SourceBinding("massive", "us-securities", 90, QualityLevel.RESEARCH, ("rest", "flat-file")),),
)
_US_EQUITY_MASSIVE_CORPORATE_ACTIONS_PRODUCT = _product(
    "reference.corporate_actions.equity.us.massive",
    "Massive US equity split and dividend events",
    DatasetLayer.SOURCE,
    {"asset_class": "equity", "region": "us", "provider": "massive", "event_family": "corporate-actions"},
    primary_time="effective_at",
    sources=(SourceBinding("massive", "us-securities", 100, QualityLevel.RESEARCH, ("rest",)),),
)
_US_EQUITY_MASSIVE_IDENTITY_PRODUCT = _product(
    "reference.identity.equity.us.massive",
    "Massive US equity identity and symbol mappings",
    DatasetLayer.REFERENCE,
    {"asset_class": "equity", "region": "us", "provider": "massive", "reference_set": "identity"},
    primary_time="effective_from",
    sources=(SourceBinding("massive", "us-securities", 100, QualityLevel.RESEARCH, ("rest",)),),
)
_US_EQUITY_RETURNS_DAILY_PRODUCT = _product(
    "market.returns.equity.us.1d",
    "US equity daily internally adjusted returns",
    DatasetLayer.CURATED,
    {"asset_class": "equity", "region": "us", "frequency": "1d"},
    primary_time="available_time",
)
_US_EQUITY_UNIVERSE_DAILY_PRODUCT = _product(
    "market.universe.equity.us.1d",
    "US equity point-in-time daily universe",
    DatasetLayer.CURATED,
    {"asset_class": "equity", "region": "us", "frequency": "1d"},
    primary_time="available_time",
)
_US_EQUITY_LIQUIDITY_DAILY_PRODUCT = _product(
    "features.liquidity.equity.us.1d",
    "US equity daily liquidity features",
    DatasetLayer.FEATURES,
    {"asset_class": "equity", "region": "us", "frequency": "1d", "feature_set": "liquidity-v1"},
    primary_time="available_time",
)
_US_EQUITY_MOMENTUM_DAILY_PRODUCT = _product(
    "features.momentum.equity.us.1d",
    "US equity daily momentum features",
    DatasetLayer.FEATURES,
    {"asset_class": "equity", "region": "us", "frequency": "1d", "feature_set": "momentum-v1"},
    primary_time="available_time",
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
BINANCE_USDM_PERPETUAL_HOURLY = ManagedDataset(
    _governed(
        _BINANCE_USDM_PERPETUAL_HOURLY_PRODUCT,
        "Point-in-time Binance USD-M USDT perpetual full-market hourly OHLCV bars.",
    ),
    "canonical/market/ohlcv/asset_class=crypto/venue=binance/product=usdm-perpetual/interval=1h",
    "market.ohlcv.v1",
    _capabilities(point_in_time_universe=True, products=("perpetual",), maximum_validation_level=3),
    quality_profile="ohlcv", minimum_publication_level=QualityLevel.BACKTEST,
)
US_EQUITY_MASSIVE_RAW_DAILY = ManagedDataset(
    _governed(
        _US_EQUITY_MASSIVE_RAW_DAILY_PRODUCT,
        "Massive US equity raw daily OHLCV bars. Full-market releases must use stable instrument identity and point-in-time reference.",
    ),
    "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw",
    "market.ohlcv.equity.us.1d.v1",
    _capabilities(point_in_time_universe=False, products=("equity",), maximum_validation_level=2),
    quality_profile="equity_ohlcv",
    minimum_publication_level=QualityLevel.RESEARCH,
)
US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY = ManagedDataset(
    _governed(
        _US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY_PRODUCT,
        "Massive US equity vendor-adjusted daily OHLCV bars for reconciliation against internally adjusted returns.",
    ),
    "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=vendor_adjusted",
    "market.ohlcv.equity.us.1d.v1",
    _capabilities(point_in_time_universe=False, products=("equity",), maximum_validation_level=2),
    quality_profile="equity_ohlcv",
    minimum_publication_level=QualityLevel.RESEARCH,
)
US_EQUITY_MASSIVE_CORPORATE_ACTIONS = ManagedDataset(
    _governed(
        _US_EQUITY_MASSIVE_CORPORATE_ACTIONS_PRODUCT,
        "Massive US equity split and cash dividend events archived for internal adjustment.",
    ),
    "reference/provider=massive/corporate_actions",
    "reference.corporate_actions.equity.us.massive.v1",
    _capabilities(point_in_time_universe=False, products=("equity",), maximum_validation_level=2),
    storage_kind=DatasetStorageKind.REFERENCE,
    quality_profile="corporate_action",
    minimum_publication_level=QualityLevel.RESEARCH,
)
US_EQUITY_MASSIVE_IDENTITY = ManagedDataset(
    _governed(
        _US_EQUITY_MASSIVE_IDENTITY_PRODUCT,
        "Massive US equity stable instrument identities, symbol mappings and quarantine records.",
    ),
    "reference/provider=massive/equity_identity",
    "reference.identity.equity.us.massive.v1",
    _capabilities(point_in_time_universe=False, products=("equity",), maximum_validation_level=2),
    storage_kind=DatasetStorageKind.REFERENCE,
    quality_profile="equity_identity",
    minimum_publication_level=QualityLevel.RESEARCH,
)
US_EQUITY_RETURNS_DAILY = ManagedDataset(
    _governed(
        _US_EQUITY_RETURNS_DAILY_PRODUCT,
        "Internally adjusted US equity split-adjusted and total-return series.",
    ),
    "curated/market/returns/asset_class=equity/region=us/interval=1d",
    "market.returns.equity.us.1d.v1",
    _capabilities(point_in_time_universe=True, products=("equity",), maximum_validation_level=3),
    quality_profile="equity_returns",
    minimum_publication_level=QualityLevel.BACKTEST,
)
US_EQUITY_UNIVERSE_DAILY = ManagedDataset(
    _governed(
        _US_EQUITY_UNIVERSE_DAILY_PRODUCT,
        "Point-in-time US equity daily universe and eligibility exclusions.",
    ),
    "curated/market/universe/asset_class=equity/region=us/frequency=1d",
    "market.universe.equity.us.1d.v1",
    _capabilities(point_in_time_universe=True, products=("equity",), maximum_validation_level=3),
    quality_profile="equity_universe",
    minimum_publication_level=QualityLevel.BACKTEST,
)
US_EQUITY_LIQUIDITY_DAILY = ManagedDataset(
    _governed(_US_EQUITY_LIQUIDITY_DAILY_PRODUCT, "Daily US equity liquidity features such as trailing ADV."),
    "features/equity/region=us/feature_set=liquidity-v1/frequency=1d",
    "features.liquidity.equity.us.1d.v1",
    _capabilities(point_in_time_universe=True, products=("equity",), maximum_validation_level=3),
    quality_profile="equity_feature",
    minimum_publication_level=QualityLevel.BACKTEST,
)
US_EQUITY_MOMENTUM_DAILY = ManagedDataset(
    _governed(
        _US_EQUITY_MOMENTUM_DAILY_PRODUCT,
        "Daily US equity cross-sectional momentum features including 12-1, 6-1 and 3-1 returns.",
    ),
    "features/equity/region=us/feature_set=momentum-v1/frequency=1d",
    "features.momentum.equity.us.1d.v1",
    _capabilities(point_in_time_universe=True, products=("equity",), maximum_validation_level=3),
    quality_profile="equity_feature",
    minimum_publication_level=QualityLevel.BACKTEST,
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
    """Compatibility handles backed by the authoritative DataProductContract objects."""

    MARKET_OHLCV_CRYPTO_BINANCE_BTC_USDT_1D = BTC_SPOT_DAILY.product
    MARKET_OHLCV_CRYPTO_BINANCE_USDM_PERPETUAL_1H = BINANCE_USDM_PERPETUAL_HOURLY.product
    MARKET_OHLCV_EQUITY_US_MASSIVE_1D_RAW = US_EQUITY_MASSIVE_RAW_DAILY.product
    MARKET_OHLCV_EQUITY_US_MASSIVE_1D_VENDOR_ADJUSTED = US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY.product
    REFERENCE_CORPORATE_ACTIONS_EQUITY_US_MASSIVE = US_EQUITY_MASSIVE_CORPORATE_ACTIONS.product
    REFERENCE_IDENTITY_EQUITY_US_MASSIVE = US_EQUITY_MASSIVE_IDENTITY.product
    MARKET_RETURNS_EQUITY_US_1D = US_EQUITY_RETURNS_DAILY.product
    MARKET_UNIVERSE_EQUITY_US_1D = US_EQUITY_UNIVERSE_DAILY.product
    FEATURES_LIQUIDITY_EQUITY_US_1D = US_EQUITY_LIQUIDITY_DAILY.product
    FEATURES_MOMENTUM_EQUITY_US_1D = US_EQUITY_MOMENTUM_DAILY.product
    ANALYTICS_VENDOR_VOLATILITY_INDEX_DERIBIT_BTC_DVOL_1D = BTC_DVOL_DAILY.product
    DERIVATIVES_OPTION_QUOTES_CRYPTO_BINANCE_BTC_USDT_1H = BTC_OPTION_QUOTES_HOURLY.product
    DERIVATIVES_OPTION_TRADES_CRYPTO_DERIBIT_BTC = BTC_DERIBIT_OPTION_TRADES.product
    DERIVATIVES_OPTION_QUOTES_CRYPTO_DERIBIT_BTC_SNAPSHOTS = BTC_DERIBIT_OPTION_QUOTES.product
    FEATURES_VOLATILITY_BTC_IV_RV_1D = BTC_IV_RV_DAILY.product
    FEATURES_VOLATILITY_SURFACE_BTC_TERM_SKEW_1H = BTC_TERM_SKEW_HOURLY.product
    FEATURES_VOLATILITY_SURFACE_BTC_DERIBIT_TRADE_TERM_SKEW_1D = BTC_DERIBIT_TERM_SKEW_DAILY.product
    MARKET_EVENTS_OPTIONS_US_SPXW = DataProductDefinition(
        DatasetKey("market.events.options.us.spxw"), "US SPXW option market events", DatasetLayer.CANONICAL,
        dimensions={"asset_class": "option", "region": "us", "underlying": "SPX",
                    "contract_family": "SPXW", "frequency": "event"},
        sources=(SourceBinding("massive", "opra", 100, QualityLevel.BACKTEST, ("rest", "flat-file")),),
    )
    CURATED_MARKET_SNAPSHOTS_OPTIONS_US_SPXW = DataProductDefinition(
        DatasetKey("curated.market_snapshots.options.us.spxw"), "US SPXW option market snapshots", DatasetLayer.CURATED,
        dimensions={"asset_class": "option", "region": "us", "underlying": "SPX",
                    "contract_family": "SPXW", "frequency": "snapshot"},
        primary_time="timestamp",
    )
    CURATED_MARKET_SLICES_OPTIONS_US_SPXW = CURATED_MARKET_SNAPSHOTS_OPTIONS_US_SPXW
