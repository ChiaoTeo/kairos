from __future__ import annotations

from ...contracts import DataProductContract, DatasetLayer, DatasetStorageKind, QualityLevel, SourceBinding
from .._helpers import _capabilities, _governed, _product


_US_EQUITY_MASSIVE_RAW_DAILY_PRODUCT = _product(
    "market.ohlcv.equity.us.massive.1d.raw",
    "Massive US equity daily raw OHLCV",
    DatasetLayer.CANONICAL,
    {"asset_class": "equity", "region": "us", "provider": "massive", "frequency": "1d", "view": "raw"},
    primary_time="available_time",
    sources=(SourceBinding("massive", "us-securities", 100, QualityLevel.WORKSPACE, ("rest", "flat-file")),),
)
_US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY_PRODUCT = _product(
    "market.ohlcv.equity.us.massive.1d.vendor_adjusted",
    "Massive US equity daily vendor-adjusted OHLCV",
    DatasetLayer.CANONICAL,
    {
        "asset_class": "equity",
        "region": "us",
        "provider": "massive",
        "frequency": "1d",
        "view": "vendor-adjusted",
    },
    primary_time="available_time",
    sources=(SourceBinding("massive", "us-securities", 90, QualityLevel.WORKSPACE, ("rest", "flat-file")),),
)
_US_EQUITY_MASSIVE_RAW_HOURLY_PRODUCT = _product(
    "market.ohlcv.equity.us.massive.1h.raw",
    "Massive US equity hourly raw OHLCV",
    DatasetLayer.CANONICAL,
    {
        "asset_class": "equity",
        "region": "us",
        "provider": "massive",
        "frequency": "1h",
        "view": "raw",
        "universe": "full-market",
    },
    primary_time="available_time",
    sources=(SourceBinding("massive", "us-securities", 100, QualityLevel.WORKSPACE, ("rest",)),),
)
_US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY_PRODUCT = _product(
    "market.ohlcv.equity.us.massive.1h.adjusted",
    "Massive US equity hourly adjusted OHLCV",
    DatasetLayer.CANONICAL,
    {
        "asset_class": "equity",
        "region": "us",
        "provider": "massive",
        "frequency": "1h",
        "view": "adjusted",
        "universe": "full-market",
    },
    primary_time="available_time",
    sources=(SourceBinding("massive", "us-securities", 90, QualityLevel.WORKSPACE, ("rest",)),),
)
_US_OPTION_MASSIVE_RAW_HOURLY_PRODUCT = _product(
    "market.ohlcv.option.us.massive.1h.raw",
    "Massive US option hourly raw OHLCV",
    DatasetLayer.CANONICAL,
    {
        "asset_class": "option",
        "region": "us",
        "provider": "massive",
        "frequency": "1h",
        "view": "raw",
        "universe": "full-market-or-explicit-contracts",
    },
    primary_time="available_time",
    sources=(SourceBinding("massive", "opra", 100, QualityLevel.WORKSPACE, ("flat-file", "rest")),),
)
_US_EQUITY_MASSIVE_CORPORATE_ACTIONS_PRODUCT = _product(
    "reference.corporate_actions.equity.us.massive",
    "Massive US equity split and dividend events",
    DatasetLayer.SOURCE,
    {"asset_class": "equity", "region": "us", "provider": "massive", "event_family": "corporate-actions"},
    primary_time="effective_at",
    sources=(SourceBinding("massive", "us-securities", 100, QualityLevel.WORKSPACE, ("rest",)),),
)
_US_EQUITY_MASSIVE_IDENTITY_PRODUCT = _product(
    "reference.identity.equity.us.massive",
    "Massive US equity identity and symbol mappings",
    DatasetLayer.REFERENCE,
    {"asset_class": "equity", "region": "us", "provider": "massive", "reference_set": "identity"},
    primary_time="effective_from",
    sources=(SourceBinding("massive", "us-securities", 100, QualityLevel.WORKSPACE, ("rest",)),),
)


US_EQUITY_MASSIVE_RAW_DAILY = DataProductContract(
    _governed(
        _US_EQUITY_MASSIVE_RAW_DAILY_PRODUCT,
        "Massive US equity raw daily OHLCV bars. Full-market releases must use stable instrument identity and point-in-time reference.",
    ),
    "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw",
    "market.ohlcv.equity.us.1d.v1",
    _capabilities(point_in_time_universe=False, products=("equity",), maximum_validation_level=2),
    quality_profile="equity_ohlcv",
    minimum_publication_level=QualityLevel.WORKSPACE,
)
US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY = DataProductContract(
    _governed(
        _US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY_PRODUCT,
        "Massive US equity vendor-adjusted daily OHLCV bars for reconciliation against internally adjusted returns.",
    ),
    "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=vendor_adjusted",
    "market.ohlcv.equity.us.1d.v1",
    _capabilities(point_in_time_universe=False, products=("equity",), maximum_validation_level=2),
    quality_profile="equity_ohlcv",
    minimum_publication_level=QualityLevel.WORKSPACE,
)
US_EQUITY_MASSIVE_RAW_HOURLY = DataProductContract(
    _governed(
        _US_EQUITY_MASSIVE_RAW_HOURLY_PRODUCT,
        "Massive US equity raw hourly OHLCV bars. Full-market acquisition discovers active common stocks from Massive reference data.",
    ),
    "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1h/view=raw",
    "market.ohlcv.equity.us.1h.v1",
    _capabilities(point_in_time_universe=True, products=("equity",), maximum_validation_level=2),
    quality_profile="ohlcv",
    minimum_publication_level=QualityLevel.WORKSPACE,
)
US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY = DataProductContract(
    _governed(
        _US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY_PRODUCT,
        "Massive US equity adjusted hourly OHLCV bars. Full-market acquisition discovers active common stocks from Massive reference data.",
    ),
    "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1h/view=adjusted",
    "market.ohlcv.equity.us.1h.v1",
    _capabilities(point_in_time_universe=True, products=("equity",), maximum_validation_level=2),
    quality_profile="ohlcv",
    minimum_publication_level=QualityLevel.WORKSPACE,
)
US_OPTION_MASSIVE_RAW_HOURLY = DataProductContract(
    _governed(
        _US_OPTION_MASSIVE_RAW_HOURLY_PRODUCT,
        "Massive US option raw hourly OHLCV bars from OPRA minute aggregates or explicit REST option contracts.",
    ),
    "canonical/market/ohlcv/asset_class=option/region=us/provider=massive/interval=1h/view=raw",
    "market.ohlcv.option.us.1h.v1",
    _capabilities(point_in_time_universe=False, products=("option",), maximum_validation_level=2),
    quality_profile="ohlcv",
    minimum_publication_level=QualityLevel.WORKSPACE,
)
US_EQUITY_MASSIVE_CORPORATE_ACTIONS = DataProductContract(
    _governed(
        _US_EQUITY_MASSIVE_CORPORATE_ACTIONS_PRODUCT,
        "Massive US equity split and cash dividend events archived for internal adjustment.",
    ),
    "reference/provider=massive/corporate_actions",
    "reference.corporate_actions.equity.us.massive.v1",
    _capabilities(point_in_time_universe=False, products=("equity",), maximum_validation_level=2),
    storage_kind=DatasetStorageKind.REFERENCE,
    quality_profile="corporate_action",
    minimum_publication_level=QualityLevel.WORKSPACE,
)
US_EQUITY_MASSIVE_IDENTITY = DataProductContract(
    _governed(
        _US_EQUITY_MASSIVE_IDENTITY_PRODUCT,
        "Massive US equity stable instrument identities, symbol mappings and quarantine records.",
    ),
    "reference/provider=massive/equity_identity",
    "reference.identity.equity.us.massive.v1",
    _capabilities(point_in_time_universe=False, products=("equity",), maximum_validation_level=2),
    storage_kind=DatasetStorageKind.REFERENCE,
    quality_profile="equity_identity",
    minimum_publication_level=QualityLevel.WORKSPACE,
)


def equity_daily_builder(root, client, *, view: str = "vendor_adjusted"):
    from kairospy.integrations.connectors.massive.datasets import MassiveEquityDailyMarketOhlcvDatasetConnector

    return MassiveEquityDailyMarketOhlcvDatasetConnector(root, client, view=view)


def equity_hourly_builder(root, client, *, view: str = "adjusted"):
    from kairospy.integrations.connectors.massive.datasets import MassiveEquityHourlyOhlcvDatasetConnector

    return MassiveEquityHourlyOhlcvDatasetConnector(root, client, view=view)


def option_hourly_builder(root, client):
    from kairospy.integrations.connectors.massive.datasets import MassiveOptionHourlyOhlcvDatasetConnector

    return MassiveOptionHourlyOhlcvDatasetConnector(root, client)


def register(registry, root, massive_config) -> None:
    from kairospy.integrations.connectors.massive.client import MassiveClient

    registry.register(equity_daily_builder(root, MassiveClient(massive_config), view="raw"), (US_EQUITY_MASSIVE_RAW_DAILY,))
    registry.register(
        equity_daily_builder(root, MassiveClient(massive_config), view="vendor_adjusted"),
        (US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY,),
    )
    registry.register(equity_hourly_builder(root, MassiveClient(massive_config), view="raw"), (US_EQUITY_MASSIVE_RAW_HOURLY,))
    registry.register(
        equity_hourly_builder(root, MassiveClient(massive_config), view="adjusted"),
        (US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY,),
    )
    registry.register(option_hourly_builder(root, MassiveClient(massive_config)), (US_OPTION_MASSIVE_RAW_HOURLY,))
