from __future__ import annotations

from ...contracts import DataProductContract, DatasetLayer, QualityLevel, SourceBinding
from .._helpers import _capabilities, _governed, _product


_BTC_DVOL_DAILY_PRODUCT = _product(
    "analytics.vendor_volatility_index.deribit.btc-dvol.1d",
    "Deribit BTC DVOL daily",
    DatasetLayer.CANONICAL,
    {"asset_class": "crypto", "venue": "deribit", "underlying": "BTC", "frequency": "1d"},
    primary_time="period_start",
    sources=(SourceBinding("deribit", "deribit", 100, QualityLevel.WORKSPACE, ("public-api",)),),
)
_BTC_DERIBIT_OPTION_TRADES_PRODUCT = _product(
    "derivatives.option_trades.crypto.deribit.btc",
    "Deribit BTC option trades",
    DatasetLayer.CANONICAL,
    {"asset_class": "option", "venue": "deribit", "underlying": "BTC", "frequency": "event"},
    sources=(SourceBinding("deribit", "deribit", 100, QualityLevel.WORKSPACE, ("public-api",)),),
)
_BTC_DERIBIT_OPTION_QUOTES_PRODUCT = _product(
    "derivatives.option_quotes.crypto.deribit.btc.snapshots",
    "Deribit BTC option snapshots",
    DatasetLayer.CANONICAL,
    {"asset_class": "option", "venue": "deribit", "underlying": "BTC", "frequency": "snapshot"},
    sources=(SourceBinding("deribit", "deribit", 100, QualityLevel.WORKSPACE, ("public-api",)),),
)


BTC_DVOL_DAILY = DataProductContract(
    _governed(_BTC_DVOL_DAILY_PRODUCT, "Deribit BTC DVOL observations for volatility analysis."),
    "canonical/analytics/vendor_volatility_indices/provider=deribit/underlying=BTC/index=DVOL/interval=1d",
    "analytics.vendor_volatility_index.v1",
    _capabilities(),
    quality_profile="generic",
)
BTC_DERIBIT_OPTION_TRADES = DataProductContract(
    _governed(_BTC_DERIBIT_OPTION_TRADES_PRODUCT, "Canonical Deribit BTC option trade events."),
    "canonical/derivatives/option_trades/asset_class=crypto/venue=deribit/underlying=BTC",
    "derivatives.option_trade.v1",
    _capabilities(
        point_in_time_universe=True,
        trade_events=True,
        trade_direction=True,
        products=("option",),
        maximum_validation_level=3,
    ),
    quality_profile="trade",
)
BTC_DERIBIT_OPTION_QUOTES = DataProductContract(
    _governed(_BTC_DERIBIT_OPTION_QUOTES_PRODUCT, "Point-in-time Deribit BTC option-chain snapshots."),
    "canonical/derivatives/option_quotes/asset_class=crypto/venue=deribit/underlying=BTC",
    "derivatives.option_chain_summary.v1",
    _capabilities(
        point_in_time_universe=True,
        synchronous_quotes=True,
        top_of_book=True,
        products=("option",),
        maximum_validation_level=3,
    ),
    quality_profile="option_snapshot",
)


def dvol_daily_builder(root):
    from kairospy.integrations.connectors.deribit.datasets import DeribitDvolDatasetConnector

    return DeribitDvolDatasetConnector(root)


def option_trades_builder(root):
    from kairospy.integrations.connectors.deribit.datasets import DeribitOptionTradesDatasetConnector

    return DeribitOptionTradesDatasetConnector(root)


def option_snapshot_builder(root):
    from kairospy.integrations.connectors.deribit.datasets import DeribitOptionSnapshotDatasetConnector

    return DeribitOptionSnapshotDatasetConnector(root)


def register(registry, root) -> None:
    registry.register(dvol_daily_builder(root), (BTC_DVOL_DAILY,))
    registry.register(option_trades_builder(root), (BTC_DERIBIT_OPTION_TRADES,))
    registry.register(option_snapshot_builder(root), (BTC_DERIBIT_OPTION_QUOTES,))
