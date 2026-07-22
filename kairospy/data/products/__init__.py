from __future__ import annotations

from ..contracts import DataProductContract, DataProductDefinition, DatasetKey, DatasetLayer, QualityLevel, SourceBinding
from .builtin.binance import (
    BINANCE_USDM_PERPETUAL_HOURLY,
    BTC_OPTION_QUOTES_HOURLY,
    BTC_SPOT_DAILY,
)
from .builtin.deribit import (
    BTC_DERIBIT_OPTION_QUOTES,
    BTC_DERIBIT_OPTION_TRADES,
    BTC_DVOL_DAILY,
)
from .builtin.massive import (
    US_EQUITY_MASSIVE_CORPORATE_ACTIONS,
    US_EQUITY_MASSIVE_IDENTITY,
    US_EQUITY_MASSIVE_RAW_DAILY,
    US_EQUITY_MASSIVE_RAW_HOURLY,
    US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY,
    US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY,
    US_OPTION_MASSIVE_RAW_HOURLY,
)
from .builtin.volatility import (
    BTC_DERIBIT_TERM_SKEW_DAILY,
    BTC_IV_RV_DAILY,
    BTC_TERM_SKEW_HOURLY,
    US_EQUITY_LIQUIDITY_DAILY,
    US_EQUITY_MOMENTUM_DAILY,
    US_EQUITY_RETURNS_DAILY,
    US_EQUITY_UNIVERSE_DAILY,
)


def capabilities_payload(dataset: DataProductContract, release_id: str) -> dict[str, object]:
    return {"capability_schema_version": 2, "dataset_id": release_id, **dict(dataset.capabilities)}


class Datasets:
    """Product handles backed by the authoritative DataProductContract objects."""

    MARKET_OHLCV_CRYPTO_BINANCE_BTC_USDT_1D = BTC_SPOT_DAILY.product
    MARKET_OHLCV_CRYPTO_BINANCE_USDM_PERPETUAL_1H = BINANCE_USDM_PERPETUAL_HOURLY.product
    MARKET_OHLCV_EQUITY_US_MASSIVE_1D_RAW = US_EQUITY_MASSIVE_RAW_DAILY.product
    MARKET_OHLCV_EQUITY_US_MASSIVE_1D_VENDOR_ADJUSTED = US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY.product
    MARKET_OHLCV_EQUITY_US_MASSIVE_1H_RAW = US_EQUITY_MASSIVE_RAW_HOURLY.product
    MARKET_OHLCV_EQUITY_US_MASSIVE_1H_VENDOR_ADJUSTED = US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY.product
    MARKET_OHLCV_OPTION_US_MASSIVE_1H_RAW = US_OPTION_MASSIVE_RAW_HOURLY.product
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
        DatasetKey("market.events.options.us.spxw"),
        "US SPXW option market events",
        DatasetLayer.CANONICAL,
        dimensions={
            "asset_class": "option",
            "region": "us",
            "underlying": "SPX",
            "contract_family": "SPXW",
            "frequency": "event",
        },
        sources=(SourceBinding("massive", "opra", 100, QualityLevel.BACKTEST, ("rest", "flat-file")),),
    )
    CURATED_MARKET_SNAPSHOTS_OPTIONS_US_SPXW = DataProductDefinition(
        DatasetKey("curated.market_snapshots.options.us.spxw"),
        "US SPXW option market snapshots",
        DatasetLayer.CURATED,
        dimensions={
            "asset_class": "option",
            "region": "us",
            "underlying": "SPX",
            "contract_family": "SPXW",
            "frequency": "snapshot",
        },
        primary_time="timestamp",
    )
    CURATED_MARKET_SLICES_OPTIONS_US_SPXW = CURATED_MARKET_SNAPSHOTS_OPTIONS_US_SPXW


__all__ = [
    "BINANCE_USDM_PERPETUAL_HOURLY",
    "BTC_DERIBIT_OPTION_QUOTES",
    "BTC_DERIBIT_OPTION_TRADES",
    "BTC_DERIBIT_TERM_SKEW_DAILY",
    "BTC_DVOL_DAILY",
    "BTC_IV_RV_DAILY",
    "BTC_OPTION_QUOTES_HOURLY",
    "BTC_SPOT_DAILY",
    "BTC_TERM_SKEW_HOURLY",
    "Datasets",
    "US_EQUITY_LIQUIDITY_DAILY",
    "US_EQUITY_MASSIVE_CORPORATE_ACTIONS",
    "US_EQUITY_MASSIVE_IDENTITY",
    "US_EQUITY_MASSIVE_RAW_DAILY",
    "US_EQUITY_MASSIVE_RAW_HOURLY",
    "US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY",
    "US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY",
    "US_EQUITY_MOMENTUM_DAILY",
    "US_EQUITY_RETURNS_DAILY",
    "US_EQUITY_UNIVERSE_DAILY",
    "US_OPTION_MASSIVE_RAW_HOURLY",
    "capabilities_payload",
]
