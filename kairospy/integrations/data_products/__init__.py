from __future__ import annotations

from pathlib import Path

from kairospy.integrations.data_products.catalog import BUILT_IN_EXTRA_PRODUCTS as LIVE_PRODUCTS

from . import binance, ccxt, deribit, hyperliquid, massive, volatility


BTC_SPOT_DAILY = binance.BTC_SPOT_DAILY
BINANCE_USDM_PERPETUAL_HOURLY = binance.BINANCE_USDM_PERPETUAL_HOURLY
BTC_OPTION_QUOTES_HOURLY = binance.BTC_OPTION_QUOTES_HOURLY
BTC_DVOL_DAILY = deribit.BTC_DVOL_DAILY
BTC_DERIBIT_OPTION_TRADES = deribit.BTC_DERIBIT_OPTION_TRADES
BTC_DERIBIT_OPTION_QUOTES = deribit.BTC_DERIBIT_OPTION_QUOTES
US_EQUITY_MASSIVE_RAW_DAILY = massive.US_EQUITY_MASSIVE_RAW_DAILY
US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY = massive.US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY
US_EQUITY_MASSIVE_RAW_HOURLY = massive.US_EQUITY_MASSIVE_RAW_HOURLY
US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY = massive.US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY
US_OPTION_MASSIVE_RAW_HOURLY = massive.US_OPTION_MASSIVE_RAW_HOURLY
US_EQUITY_MASSIVE_CORPORATE_ACTIONS = massive.US_EQUITY_MASSIVE_CORPORATE_ACTIONS
US_EQUITY_MASSIVE_IDENTITY = massive.US_EQUITY_MASSIVE_IDENTITY
BTC_IV_RV_DAILY = volatility.BTC_IV_RV_DAILY
BTC_TERM_SKEW_HOURLY = volatility.BTC_TERM_SKEW_HOURLY
BTC_DERIBIT_TERM_SKEW_DAILY = volatility.BTC_DERIBIT_TERM_SKEW_DAILY
US_EQUITY_RETURNS_DAILY = volatility.US_EQUITY_RETURNS_DAILY
US_EQUITY_UNIVERSE_DAILY = volatility.US_EQUITY_UNIVERSE_DAILY
US_EQUITY_LIQUIDITY_DAILY = volatility.US_EQUITY_LIQUIDITY_DAILY
US_EQUITY_MOMENTUM_DAILY = volatility.US_EQUITY_MOMENTUM_DAILY


ACQUIRABLE_PRODUCTS = (
    BTC_SPOT_DAILY,
    BINANCE_USDM_PERPETUAL_HOURLY,
    BTC_DVOL_DAILY,
    BTC_OPTION_QUOTES_HOURLY,
    BTC_DERIBIT_OPTION_TRADES,
    BTC_DERIBIT_OPTION_QUOTES,
)
KNOWN_PRODUCTS = (
    *ACQUIRABLE_PRODUCTS,
    BTC_IV_RV_DAILY,
    BTC_TERM_SKEW_HOURLY,
    BTC_DERIBIT_TERM_SKEW_DAILY,
    US_EQUITY_MASSIVE_RAW_DAILY,
    US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY,
    US_EQUITY_MASSIVE_CORPORATE_ACTIONS,
    US_EQUITY_MASSIVE_RAW_HOURLY,
    US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY,
    US_OPTION_MASSIVE_RAW_HOURLY,
    US_EQUITY_MASSIVE_IDENTITY,
    US_EQUITY_RETURNS_DAILY,
    US_EQUITY_UNIVERSE_DAILY,
    US_EQUITY_LIQUIDITY_DAILY,
    US_EQUITY_MOMENTUM_DAILY,
)


def integration_product_specs():
    """Return integration-provided historical Data Product contracts.

    This is the Phase 1 facade for moving provider product declarations out of
    ``kairospy.data`` without changing existing product keys yet.
    """

    return KNOWN_PRODUCTS


def integration_live_products():
    """Return integration-provided live Data Product declarations."""

    return LIVE_PRODUCTS


def register_integration_provider_builders(
    providers,
    root: str | Path,
    *,
    massive_config=None,
    progress=None,
    stop_event=None,
) -> None:
    """Register provider acquisition builders from the integration layer."""

    binance.register(providers, root, progress=progress, stop_event=stop_event)
    deribit.register(providers, root)
    if massive_config is not None:
        massive.register(providers, root, massive_config)


__all__ = [
    "ACQUIRABLE_PRODUCTS",
    "BINANCE_USDM_PERPETUAL_HOURLY",
    "BTC_DERIBIT_OPTION_QUOTES",
    "BTC_DERIBIT_OPTION_TRADES",
    "BTC_DERIBIT_TERM_SKEW_DAILY",
    "BTC_DVOL_DAILY",
    "BTC_IV_RV_DAILY",
    "BTC_OPTION_QUOTES_HOURLY",
    "BTC_SPOT_DAILY",
    "BTC_TERM_SKEW_HOURLY",
    "KNOWN_PRODUCTS",
    "LIVE_PRODUCTS",
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
    "binance",
    "ccxt",
    "deribit",
    "hyperliquid",
    "integration_live_products",
    "integration_product_specs",
    "massive",
    "register_integration_provider_builders",
    "volatility",
]
