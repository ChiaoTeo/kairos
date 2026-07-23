from __future__ import annotations

from kairospy.data.contracts import DataProductContract, DatasetLayer, QualityLevel
from kairospy.integrations.data_products._helpers import _capabilities, _governed, _product


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
_BTC_IV_RV_DAILY_PRODUCT = _product(
    "features.volatility.btc.iv-rv.1d",
    "BTC IV/RV daily features",
    DatasetLayer.FEATURES,
    {"asset_class": "crypto", "underlying": "BTC", "frequency": "1d"},
    primary_time="period_start",
)
_BTC_TERM_SKEW_HOURLY_PRODUCT = _product(
    "features.volatility_surface.btc.term-skew.1h",
    "BTC term skew hourly features",
    DatasetLayer.FEATURES,
    {"asset_class": "option", "underlying": "BTC", "frequency": "1h"},
    primary_time="period_start",
)
_BTC_DERIBIT_TERM_SKEW_DAILY_PRODUCT = _product(
    "features.volatility_surface.btc.deribit-trade-term-skew.1d",
    "Deribit BTC term skew daily",
    DatasetLayer.FEATURES,
    {"asset_class": "option", "venue": "deribit", "underlying": "BTC", "frequency": "1d"},
    primary_time="period_start",
)


US_EQUITY_RETURNS_DAILY = DataProductContract(
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
US_EQUITY_UNIVERSE_DAILY = DataProductContract(
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
US_EQUITY_LIQUIDITY_DAILY = DataProductContract(
    _governed(_US_EQUITY_LIQUIDITY_DAILY_PRODUCT, "Daily US equity liquidity features such as trailing ADV."),
    "features/equity/region=us/feature_set=liquidity-v1/frequency=1d",
    "features.liquidity.equity.us.1d.v1",
    _capabilities(point_in_time_universe=True, products=("equity",), maximum_validation_level=3),
    quality_profile="equity_feature",
    minimum_publication_level=QualityLevel.BACKTEST,
)
US_EQUITY_MOMENTUM_DAILY = DataProductContract(
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
BTC_IV_RV_DAILY = DataProductContract(
    _governed(_BTC_IV_RV_DAILY_PRODUCT, "Daily BTC implied-versus-realized volatility features."),
    "features/volatility/underlying=BTC/frequency=1d/feature_set=iv_rv_v1",
    "features.volatility.iv_rv_daily.v1",
    _capabilities(),
    quality_profile="feature",
)
BTC_TERM_SKEW_HOURLY = DataProductContract(
    _governed(_BTC_TERM_SKEW_HOURLY_PRODUCT, "Hourly BTC volatility term-skew features."),
    "features/volatility_surface/underlying=BTC/frequency=1h/feature_set=term_skew_v1",
    "features.volatility_surface.term_skew.v1",
    _capabilities(point_in_time_universe=True, products=("option",)),
    quality_profile="feature",
)
BTC_DERIBIT_TERM_SKEW_DAILY = DataProductContract(
    _governed(_BTC_DERIBIT_TERM_SKEW_DAILY_PRODUCT, "Daily BTC term-skew features derived from Deribit trades."),
    "features/volatility_surface/underlying=BTC/frequency=1d/feature_set=deribit_trade_term_skew_v1",
    "features.volatility_surface.trade_term_skew.v1",
    _capabilities(point_in_time_universe=True, products=("option",)),
    quality_profile="feature",
)
