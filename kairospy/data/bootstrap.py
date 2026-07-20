from __future__ import annotations

import json
from pathlib import Path

from kairospy.connectors.binance.datasets import (
    BinanceOptionQuotesDatasetConnector, BinanceSpotDatasetConnector,
    BinanceUsdmPerpetualHourlyDatasetConnector,
)
from kairospy.connectors.binance.historical_archive import BinanceUsdmPerpetualHourlyArchiveProvider
from kairospy.connectors.deribit.datasets import (
    DeribitDvolDatasetConnector, DeribitOptionSnapshotDatasetConnector, DeribitOptionTradesDatasetConnector,
)

from .acquisition import ProviderRegistry
from .catalog import DataCatalog
from .contracts import (
    DatasetKey, DatasetLayer, DataProductDefinition, DataProductContract, DatasetStorageKind, QualityLevel, SourceBinding,
)
from .products import (
    BTC_DERIBIT_OPTION_QUOTES, BTC_DERIBIT_OPTION_TRADES, BTC_DVOL_DAILY, BTC_OPTION_QUOTES_HOURLY,
    BTC_SPOT_DAILY, BINANCE_USDM_PERPETUAL_HOURLY, BTC_DERIBIT_TERM_SKEW_DAILY,
    BTC_IV_RV_DAILY, BTC_TERM_SKEW_HOURLY, US_EQUITY_LIQUIDITY_DAILY, US_EQUITY_MASSIVE_RAW_DAILY,
    US_EQUITY_MASSIVE_CORPORATE_ACTIONS, US_EQUITY_MASSIVE_IDENTITY,
    US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY,
    US_EQUITY_MOMENTUM_DAILY, US_EQUITY_RETURNS_DAILY, US_EQUITY_UNIVERSE_DAILY,
)


DEFAULT_ACQUIRABLE_PRODUCTS = (
    BTC_SPOT_DAILY, BINANCE_USDM_PERPETUAL_HOURLY, BTC_DVOL_DAILY, BTC_OPTION_QUOTES_HOURLY,
    BTC_DERIBIT_OPTION_TRADES, BTC_DERIBIT_OPTION_QUOTES,
)
KNOWN_PRODUCTS = (*DEFAULT_ACQUIRABLE_PRODUCTS, BTC_IV_RV_DAILY, BTC_TERM_SKEW_HOURLY,
                  BTC_DERIBIT_TERM_SKEW_DAILY, US_EQUITY_MASSIVE_RAW_DAILY,
                  US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY, US_EQUITY_MASSIVE_CORPORATE_ACTIONS,
                  US_EQUITY_MASSIVE_IDENTITY, US_EQUITY_RETURNS_DAILY, US_EQUITY_UNIVERSE_DAILY,
                  US_EQUITY_LIQUIDITY_DAILY, US_EQUITY_MOMENTUM_DAILY)


def register_default_products(root: str | Path = "data") -> DataCatalog:
    catalog = DataCatalog(root)
    for dataset in KNOWN_PRODUCTS:
        try:
            catalog.register_product_spec(dataset, enrich=True)
        except ValueError as error:
            if "conflicting data product contract" not in str(error):
                raise
            catalog.update_product_spec(
                dataset,
                actor="catalog-bootstrap",
                reason="synchronize built-in data product contract",
            )
    catalog.save()
    return catalog


def register_configured_products(root: str | Path, config_path: str | Path) -> DataCatalog:
    catalog = DataCatalog(root)
    for spec in configured_product_specs(config_path):
        catalog.register_product_spec(spec, enrich=True)
    catalog.save()
    return catalog


def default_provider_registry(root: str | Path = "data", *, connector_config: str | Path | None = None,
                              progress=None, stop_event=None) -> ProviderRegistry:
    providers = ProviderRegistry()
    specs = KNOWN_PRODUCTS
    for connector in (
        BinanceSpotDatasetConnector(root), BinanceUsdmPerpetualHourlyDatasetConnector(
            root, BinanceUsdmPerpetualHourlyArchiveProvider(progress=progress, stop_event=stop_event),
        ),
        BinanceOptionQuotesDatasetConnector(root),
        DeribitDvolDatasetConnector(root), DeribitOptionTradesDatasetConnector(root),
        DeribitOptionSnapshotDatasetConnector(root),
    ):
        providers.register(connector, tuple(spec for spec in specs if connector.supports(str(spec.key))))
    if connector_config is not None:
        from kairospy.connectors.massive.datasets import MassiveEquityDailyOhlcvProductConfig, MassiveOptionProductConfig
        configured = {str(spec.key): spec for spec in configured_product_specs(connector_config)}
        for raw in _massive_option_products(connector_config):
            spec = configured[str(raw["logical_key"])]
            connector = _ConfiguredMassiveConnector(
                root, MassiveOptionProductConfig(
                    str(raw["logical_key"]), str(raw["underlying"]),
                    tuple(str(item) for item in raw["option_tickers"]),
                    str(raw["underlying_reference_ticker"]) if raw.get("underlying_reference_ticker") else None,
                ),
            )
            providers.register(connector, (spec,))
        for raw in _massive_equity_products(connector_config):
            spec = configured[str(raw["logical_key"])]
            connector = _ConfiguredMassiveEquityConnector(
                root, MassiveEquityDailyOhlcvProductConfig(
                    str(raw["logical_key"]), str(raw["ticker"]), str(raw.get("view", "vendor_adjusted")),
                ),
            )
            providers.register(connector, (spec,))
    return providers


def configured_product_specs(config_path: str | Path) -> tuple[DataProductContract, ...]:
    specs = []
    for raw in _massive_option_products(config_path):
        logical_key = str(raw["logical_key"])
        product = DataProductDefinition(
            DatasetKey(logical_key), str(raw.get("title") or logical_key),
            DatasetLayer.CANONICAL,
            str(raw.get("description") or f"Canonical Massive option market events for {raw['underlying']}."),
            {str(key): str(value) for key, value in dict(raw.get("dimensions", {})).items()},
            str(raw.get("primary_time", "available_time")),
            sources=(SourceBinding(
                "massive", "opra", int(raw.get("priority", 100)), QualityLevel.BACKTEST, ("rest",),
            ),),
            owner=str(raw.get("owner", "data-platform")),
            source_policy_version=str(raw.get("source_policy_version", "priority-v1")),
        )
        specs.append(DataProductContract(
            product,
            str(raw.get("relative_path") or f"canonical/market/product={logical_key}"),
            str(raw.get("schema_id", "market.event_envelope.v1")),
            dict(raw.get("capabilities", {
                "point_in_time_universe": True,
                "top_of_book": True,
                "trade_events": True,
                "supported_products": ["option"],
            })),
            DatasetStorageKind(str(raw.get("storage_kind", DatasetStorageKind.MARKET_EVENTS.value))),
            str(raw.get("layout_version", "1")),
            str(raw.get("quality_profile", "market_event")),
            QualityLevel(str(raw.get("minimum_publication_level", QualityLevel.BACKTEST.value))),
        ))
    for raw in _massive_equity_products(config_path):
        logical_key = str(raw["logical_key"])
        view = str(raw.get("view", "vendor_adjusted"))
        product = DataProductDefinition(
            DatasetKey(logical_key), str(raw.get("title") or logical_key), DatasetLayer.CANONICAL,
            str(raw.get("description") or f"Massive US equity {view} daily OHLCV for {raw['ticker']}."),
            {**{"asset_class": "equity", "region": "us", "provider": "massive", "frequency": "1d", "view": view},
             **{str(key): str(value) for key, value in dict(raw.get("dimensions", {})).items()}},
            str(raw.get("primary_time", "available_time")),
            sources=(SourceBinding(
                "massive", "us-securities", int(raw.get("priority", 100)),
                QualityLevel(str(raw.get("source_quality_level", QualityLevel.STUDY.value))),
                ("rest",),
            ),),
            owner=str(raw.get("owner", "data-platform")),
            source_policy_version=str(raw.get("source_policy_version", "priority-v1")),
        )
        specs.append(DataProductContract(
            product,
            str(raw.get("relative_path") or
                f"canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view={view}"),
            str(raw.get("schema_id", "market.ohlcv.equity.us.1d.v1")),
            dict(raw.get("capabilities", {
                "point_in_time_universe": False,
                "supported_products": ["equity"],
                "maximum_validation_level": 2,
            })),
            DatasetStorageKind(str(raw.get("storage_kind", DatasetStorageKind.TABULAR.value))),
            str(raw.get("layout_version", "1")),
            str(raw.get("quality_profile", "equity_ohlcv")),
            QualityLevel(str(raw.get("minimum_publication_level", QualityLevel.STUDY.value))),
        ))
    return tuple(specs)


class _ConfiguredMassiveConnector:
    provider = "massive"

    def __init__(self, root, config) -> None:
        self.root, self.config = root, config

    def supports(self, logical_key: str) -> bool:
        return logical_key == self.config.logical_key

    def estimate(self, request):
        from kairospy.data.acquisition import AcquisitionEstimate
        days = sum(max(1, (item.end.date() - item.start.date()).days + 1) for item in request.missing)
        return AcquisitionEstimate(days * len(self.config.option_tickers) * 3 + 6, cost_class="entitled")

    def acquire(self, request):
        from kairospy.connectors.massive.client import MassiveClient
        from kairospy.connectors.massive.datasets import MassiveOptionEventsDatasetConnector
        return MassiveOptionEventsDatasetConnector(
            self.root, MassiveClient(_massive_config_for_project()), self.config,
        ).acquire(request)


class _ConfiguredMassiveEquityConnector:
    provider = "massive"

    def __init__(self, root, config) -> None:
        self.root, self.config = root, config

    def supports(self, logical_key: str) -> bool:
        return logical_key == self.config.logical_key

    def estimate(self, request):
        from kairospy.data.acquisition import AcquisitionEstimate
        days = sum(max(1, (item.end.date() - item.start.date()).days + 1) for item in request.missing)
        return AcquisitionEstimate(days, cost_class="entitled-rest-bounded-ticker")

    def acquire(self, request):
        from kairospy.connectors.massive.client import MassiveClient
        from kairospy.connectors.massive.datasets import MassiveEquityDailyOhlcvDatasetConnector
        return MassiveEquityDailyOhlcvDatasetConnector(
            self.root, MassiveClient(_massive_config_for_project()), self.config,
        ).acquire(request)


def _massive_config_for_project():
    from kairospy.configuration import ConfigError, load_project_config_or_none
    from kairospy.connectors.massive.config import MassiveConfig

    config = load_project_config_or_none()
    if config is not None:
        try:
            return config.massive_config()
        except ConfigError:
            pass
    return MassiveConfig.from_env()


def _massive_option_products(path: str | Path) -> tuple[dict[str, object], ...]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    products = value.get("massive_option_products") if isinstance(value, dict) else None
    if products is None:
        return ()
    if not isinstance(products, list):
        raise ValueError("connector config massive_option_products must be a list")
    return tuple(dict(item) for item in products)


def _massive_equity_products(path: str | Path) -> tuple[dict[str, object], ...]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    products = value.get("massive_equity_products") if isinstance(value, dict) else None
    if products is None:
        return ()
    if not isinstance(products, list):
        raise ValueError("connector config massive_equity_products must be a list")
    return tuple(dict(item) for item in products)
