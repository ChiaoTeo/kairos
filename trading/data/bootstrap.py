from __future__ import annotations

import json
import os
from pathlib import Path

from trading.adapters.binance.datasets import BinanceOptionQuotesDatasetConnector, BinanceSpotDatasetConnector
from trading.adapters.deribit.datasets import (
    DeribitDvolDatasetConnector, DeribitOptionSnapshotDatasetConnector, DeribitOptionTradesDatasetConnector,
)

from .acquisition import ProviderRegistry
from .catalog import DataCatalog
from .models import (
    DatasetKey, DatasetLayer, DatasetProduct, DatasetProductSpec, DatasetStorageKind, QualityLevel, SourceBinding,
)
from .products import (
    BTC_DERIBIT_OPTION_QUOTES, BTC_DERIBIT_OPTION_TRADES, BTC_DVOL_DAILY, BTC_OPTION_QUOTES_HOURLY,
    BTC_SPOT_DAILY, BTC_DERIBIT_TERM_SKEW_DAILY, BTC_IV_RV_DAILY, BTC_TERM_SKEW_HOURLY,
)


DEFAULT_ACQUIRABLE_PRODUCTS = (
    BTC_SPOT_DAILY, BTC_DVOL_DAILY, BTC_OPTION_QUOTES_HOURLY,
    BTC_DERIBIT_OPTION_TRADES, BTC_DERIBIT_OPTION_QUOTES,
)
KNOWN_PRODUCTS = (*DEFAULT_ACQUIRABLE_PRODUCTS, BTC_IV_RV_DAILY, BTC_TERM_SKEW_HOURLY,
                  BTC_DERIBIT_TERM_SKEW_DAILY)


def register_default_products(root: str | Path = "data") -> DataCatalog:
    catalog = DataCatalog(root)
    for dataset in KNOWN_PRODUCTS:
        try:
            catalog.register_product_spec(dataset, enrich=True)
        except ValueError as error:
            if "conflicting dataset product spec" not in str(error):
                raise
            catalog.update_product_spec(
                dataset,
                actor="catalog-bootstrap",
                reason="synchronize built-in ProductSpec contract",
            )
    catalog.save()
    return catalog


def register_configured_products(root: str | Path, config_path: str | Path) -> DataCatalog:
    catalog = DataCatalog(root)
    for spec in configured_product_specs(config_path):
        catalog.register_product_spec(spec, enrich=True)
    catalog.save()
    return catalog


def default_provider_registry(root: str | Path = "data", *, connector_config: str | Path | None = None) -> ProviderRegistry:
    providers = ProviderRegistry()
    specs = KNOWN_PRODUCTS
    for connector in (
        BinanceSpotDatasetConnector(root), BinanceOptionQuotesDatasetConnector(root),
        DeribitDvolDatasetConnector(root), DeribitOptionTradesDatasetConnector(root),
        DeribitOptionSnapshotDatasetConnector(root),
    ):
        providers.register(connector, tuple(spec for spec in specs if connector.supports(str(spec.key))))
    if connector_config is not None:
        from trading.adapters.massive.datasets import MassiveOptionProductConfig
        for raw, spec in zip(_massive_products(connector_config), configured_product_specs(connector_config)):
            connector = _ConfiguredMassiveConnector(
                root, MassiveOptionProductConfig(
                    str(raw["logical_key"]), str(raw["underlying"]),
                    tuple(str(item) for item in raw["option_tickers"]),
                    str(raw["underlying_reference_ticker"]) if raw.get("underlying_reference_ticker") else None,
                ),
            )
            providers.register(connector, (spec,))
    return providers


def configured_product_specs(config_path: str | Path) -> tuple[DatasetProductSpec, ...]:
    specs = []
    for raw in _massive_products(config_path):
        logical_key = str(raw["logical_key"])
        product = DatasetProduct(
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
        specs.append(DatasetProductSpec(
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
    return tuple(specs)


class _ConfiguredMassiveConnector:
    provider = "massive"

    def __init__(self, root, config) -> None:
        self.root, self.config = root, config

    def supports(self, logical_key: str) -> bool:
        return logical_key == self.config.logical_key

    def estimate(self, request):
        from trading.data.acquisition import AcquisitionEstimate
        days = sum(max(1, (item.end.date() - item.start.date()).days + 1) for item in request.missing)
        return AcquisitionEstimate(days * len(self.config.option_tickers) * 3 + 6, cost_class="entitled")

    def acquire(self, request):
        if not os.environ.get("MASSIVE_API_KEY"):
            raise RuntimeError("MASSIVE_API_KEY is required to acquire this planned Massive dataset")
        from trading.adapters.massive.client import MassiveClient
        from trading.adapters.massive.config import MassiveConfig
        from trading.adapters.massive.datasets import MassiveOptionEventsDatasetConnector
        return MassiveOptionEventsDatasetConnector(
            self.root, MassiveClient(MassiveConfig.from_env()), self.config,
        ).acquire(request)


def _massive_products(path: str | Path) -> tuple[dict[str, object], ...]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    products = value.get("massive_option_products") if isinstance(value, dict) else None
    if not isinstance(products, list) or not products:
        raise ValueError("connector config requires a non-empty massive_option_products list")
    return tuple(dict(item) for item in products)
