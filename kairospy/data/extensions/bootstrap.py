from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from kairospy.data.acquisition import ProviderRegistry
from kairospy.data.catalog import DataCatalog
from kairospy.data.contracts import (
    DatasetKey, DatasetLayer, DataProductDefinition, DataProductContract, DatasetStorageKind, QualityLevel, SourceBinding,
)
from kairospy.data.products.builtin import ACQUIRABLE_PRODUCTS as DEFAULT_ACQUIRABLE_PRODUCTS, KNOWN_PRODUCTS
from kairospy.data.products.builtin import binance as builtin_binance_products
from kairospy.data.products.builtin import deribit as builtin_deribit_products
from kairospy.data.products.builtin import massive as builtin_massive_products
from .provider_extensions import CONFIG_PATH_KEY, provider_extension_specs, register_provider_extensions


def register_default_products(root: str | Path = DEFAULT_LAKE_ROOT) -> DataCatalog:
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


def register_configured_products(root: str | Path, config: str | Path | Mapping[str, Any] | None = None) -> DataCatalog:
    catalog = DataCatalog(root)
    for spec in configured_product_specs(_configured_data_products(config)):
        catalog.register_product_spec(spec, enrich=True)
    catalog.save()
    return catalog


def default_provider_registry(
    root: str | Path = DEFAULT_LAKE_ROOT,
    *,
    data_product_config: str | Path | Mapping[str, Any] | None = None,
    progress=None,
    stop_event=None,
) -> ProviderRegistry:
    providers = ProviderRegistry()
    builtin_binance_products.register(providers, root, progress=progress, stop_event=stop_event)
    builtin_deribit_products.register(providers, root)
    configured_config = _configured_data_products(data_product_config)
    if configured_config:
        from kairospy.integrations.connectors.massive.datasets import MassiveEquityDailyOhlcvProductConfig, MassiveOptionProductConfig
        configured = {str(spec.key): spec for spec in configured_product_specs(configured_config)}
        for raw in _massive_option_products(configured_config):
            spec = configured[str(raw["logical_key"])]
            connector = _ConfiguredMassiveConnector(
                root, MassiveOptionProductConfig(
                    str(raw["logical_key"]), str(raw["underlying"]),
                    tuple(str(item) for item in raw["option_tickers"]),
                    str(raw["underlying_reference_ticker"]) if raw.get("underlying_reference_ticker") else None,
                ),
            )
            providers.register(connector, (spec,))
        for raw in _massive_equity_products(configured_config):
            spec = configured[str(raw["logical_key"])]
            connector = _ConfiguredMassiveEquityConnector(
                root, MassiveEquityDailyOhlcvProductConfig(
                    str(raw["logical_key"]), str(raw["ticker"]), str(raw.get("view", "vendor_adjusted")),
                ),
            )
            providers.register(connector, (spec,))
        register_provider_extensions(root, configured_config, providers)
    from kairospy.infrastructure.configuration import ConfigError
    try:
        builtin_massive_products.register(providers, root, _massive_config_for_project())
    except (ConfigError, RuntimeError, ValueError):
        pass
    return providers


def configured_product_specs(config: str | Path | Mapping[str, Any] | None = None) -> tuple[DataProductContract, ...]:
    configured = _configured_data_products(config)
    specs = []
    for raw in _massive_option_products(configured):
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
    for raw in _massive_equity_products(configured):
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
                QualityLevel(str(raw.get("source_quality_level", QualityLevel.WORKSPACE.value))),
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
            QualityLevel(str(raw.get("minimum_publication_level", QualityLevel.WORKSPACE.value))),
        ))
    specs.extend(provider_extension_specs(configured))
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
        from kairospy.integrations.connectors.massive.client import MassiveClient
        from kairospy.integrations.connectors.massive.datasets import MassiveOptionEventsDatasetConnector
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
        from kairospy.integrations.connectors.massive.client import MassiveClient
        from kairospy.integrations.connectors.massive.datasets import MassiveEquityDailyOhlcvDatasetConnector
        return MassiveEquityDailyOhlcvDatasetConnector(
            self.root, MassiveClient(_massive_config_for_project()), self.config,
        ).acquire(request)


def _massive_config_for_project():
    from kairospy.infrastructure.configuration import ConfigError, load_dotenv_file, load_project_config_or_none
    from kairospy.integrations.config import resolve_massive_marketdata_config
    from kairospy.integrations.connectors.massive.config import MassiveConfig

    config = load_project_config_or_none()
    if config is not None:
        try:
            return resolve_massive_marketdata_config(config)
        except ConfigError:
            pass
    load_dotenv_file()
    return MassiveConfig.from_env()


def _configured_data_products(config: str | Path | Mapping[str, Any] | None = None) -> dict[str, Any]:
    if config is None:
        project_config = _project_config_data_products()
        return project_config
    if isinstance(config, Mapping):
        return {str(key): value for key, value in dict(config).items()}
    return _provider_config_file(Path(config))


def _project_config_data_products() -> dict[str, Any]:
    from kairospy.infrastructure.configuration import load_project_config_or_none

    config = load_project_config_or_none()
    if config is None:
        return {}
    payload: dict[str, Any] = {
        "massive_option_products": [],
        "massive_equity_products": [],
        "provider_extensions": [],
    }
    tables = config.get("data_products", {})
    if isinstance(tables, dict):
        for name, value in tables.items():
            if not isinstance(value, dict):
                continue
            raw = dict(value)
            raw.setdefault("logical_key", raw.get("key") or name)
            kind = str(raw.pop("kind", raw.pop("type", "")) or "").strip()
            if kind in {"massive_option", "massive_option_product"}:
                payload["massive_option_products"].append(raw)
            elif kind in {"massive_equity", "massive_equity_product"}:
                payload["massive_equity_products"].append(raw)
            elif kind in {"external_process", "process", "python", "provider_extension"}:
                payload["provider_extensions"].append(raw)
    extensions = config.get("provider_extensions", {})
    if isinstance(extensions, dict):
        payload["provider_extensions"].extend(dict(value) for value in extensions.values() if isinstance(value, dict))
    result = {key: value for key, value in payload.items() if value}
    if result:
        result[CONFIG_PATH_KEY] = str(config.path)
    return result


def _provider_config_file(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        return {}
    payload = dict(value)
    payload[CONFIG_PATH_KEY] = str(path.expanduser().resolve())
    return payload


def _massive_option_products(config: Mapping[str, Any]) -> tuple[dict[str, object], ...]:
    value = dict(config)
    products = value.get("massive_option_products") if isinstance(value, dict) else None
    if products is None:
        return ()
    if not isinstance(products, list):
        raise ValueError("connector config massive_option_products must be a list")
    return tuple(dict(item) for item in products)


def _massive_equity_products(config: Mapping[str, Any]) -> tuple[dict[str, object], ...]:
    value = dict(config)
    products = value.get("massive_equity_products") if isinstance(value, dict) else None
    if products is None:
        return ()
    if not isinstance(products, list):
        raise ValueError("connector config massive_equity_products must be a list")
    return tuple(dict(item) for item in products)
