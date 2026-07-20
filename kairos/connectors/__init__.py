"""External system connectors for market data, reference data, execution, and transfers."""

from __future__ import annotations

import importlib
import sys


_ALIASED_MODULES = (
    "binance",
    "deribit",
    "ibkr",
    "market_data_router",
    "massive",
    "simulated",
    "transfer",
)

_ALIASED_SUBMODULES = (
    "binance.account_gateway",
    "binance.datasets",
    "binance.execution_gateway",
    "binance.funding_settlement",
    "binance.funding_ingestion",
    "binance.historical_archive",
    "binance.market_data_client",
    "binance.market_stream",
    "binance.option_market_snapshot",
    "binance.options_archive",
    "binance.order_book",
    "binance.order_recovery",
    "binance.reference_data",
    "binance.request_signing",
    "binance.rest_transport",
    "binance.stream",
    "binance.user_data_stream",
    "deribit.datasets",
    "deribit.historical",
    "deribit.option_chain",
    "deribit.trade_history",
    "ibkr.account_gateway",
    "ibkr.execution_gateway",
    "ibkr.ingestion",
    "ibkr.market_data_client",
    "ibkr.reference_data",
    "ibkr.research",
    "ibkr.session",
    "massive.client",
    "massive.config",
    "massive.corporate_actions",
    "massive.curated",
    "massive.close_implied_volatility",
    "massive.daily_ohlcv",
    "massive.datasets",
    "massive.decoder",
    "massive.entitlement_diagnostics",
    "massive.equity_daily_ohlcv",
    "massive.equity_identity",
    "massive.pipeline",
    "massive.reference",
    "massive.reference_pipeline",
    "massive.reference_store",
    "massive.vendor_archive",
    "massive.websocket",
    "transfer.bank",
    "transfer.binance",
)

_CONNECTOR_SUBMODULES = {
    "massive.close_implied_volatility",
    "massive.daily_ohlcv",
    "massive.datasets",
    "massive.entitlement_diagnostics",
    "massive.equity_daily_ohlcv",
}


def _install_connector_aliases() -> None:
    for name in _ALIASED_MODULES:
        source = f"kairos.connectors.{name}" if name == "massive" else f"kairos.adapters.{name}"
        sys.modules.setdefault(f"{__name__}.{name}", importlib.import_module(source))
    for name in _ALIASED_SUBMODULES:
        source = f"kairos.connectors.{name}" if name in _CONNECTOR_SUBMODULES else f"kairos.adapters.{name}"
        sys.modules.setdefault(f"{__name__}.{name}", importlib.import_module(source))


_install_connector_aliases()


__all__ = list(_ALIASED_MODULES)
