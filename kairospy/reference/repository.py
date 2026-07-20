from __future__ import annotations

import json
from pathlib import Path

from kairospy.domain.product import (
    CryptoOptionSpec, CryptoSpotSpec, EquitySpec, FutureSpec, IndexSpec,
    ListedOptionSpec, PerpetualSpec, ProductType, TokenizedEquitySpec,
)
from kairospy.storage.codec import from_primitive, to_primitive

from .catalog import ReferenceCatalog
from .contracts import (
    AssetDefinition, BenchmarkDefinition, ContractSeries, EconomicProduct,
    EntityDefinition, ExecutionRoute, InstrumentDefinition,
    InstrumentReference, ListingDefinition, LocationDefinition,
    NetworkAssetDefinition, NetworkDefinition, ProviderSymbolMapping,
    SettlementRail, SettlementTermsDefinition,
    VenueDefinition,
)


SCHEMA_VERSION = 2


class ReferenceCatalogRepository:
    def __init__(self, path: str | Path = "data/reference/catalog.json") -> None:
        self.path = Path(path)

    def save(self, catalog: ReferenceCatalog) -> Path:
        value = {
            "schema_version": SCHEMA_VERSION,
            "assets": to_primitive(catalog.assets.values()),
            "entities": to_primitive(catalog.entities.values()),
            "venues": to_primitive(catalog.venues.values()),
            "benchmarks": to_primitive(catalog.benchmarks.values()),
            "products": to_primitive(catalog.products.values()),
            "series": to_primitive(catalog.series.values()),
            "instruments": [instrument_to_primitive(item) for item in catalog.instruments.values()],
            "listings": to_primitive(catalog.listings.values()),
            "routes": to_primitive(catalog.routes.values()),
            "networks": to_primitive(catalog.networks.values()),
            "network_assets": to_primitive(catalog.network_assets.values()),
            "rails": to_primitive(catalog.rails.values()),
            "locations": to_primitive(catalog.locations.values()),
            "settlements": to_primitive(catalog.settlements.values()),
            "mappings": to_primitive(catalog.mappings()),
            "references": to_primitive(catalog.all_references()),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return self.path

    def load(self) -> ReferenceCatalog:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported reference catalog schema version")
        catalog = ReferenceCatalog()
        _load_many(catalog.assets, value["assets"], AssetDefinition)
        _load_many(catalog.entities, value["entities"], EntityDefinition)
        _load_many(catalog.venues, value.get("venues", ()), VenueDefinition)
        _load_many(catalog.benchmarks, value["benchmarks"], BenchmarkDefinition)
        _load_many(catalog.products, value["products"], EconomicProduct)
        _load_many(catalog.series, value["series"], ContractSeries)
        for item in value["instruments"]:
            catalog.instruments.add(instrument_from_primitive(item))
        _load_many(catalog.listings, value["listings"], ListingDefinition)
        _load_many(catalog.routes, value["routes"], ExecutionRoute)
        _load_many(catalog.networks, value["networks"], NetworkDefinition)
        _load_many(catalog.network_assets, value["network_assets"], NetworkAssetDefinition)
        _load_many(catalog.rails, value["rails"], SettlementRail)
        _load_many(catalog.locations, value["locations"], LocationDefinition)
        _load_many(catalog.settlements, value.get("settlements", ()), SettlementTermsDefinition)
        for item in value["mappings"]:
            catalog.add_mapping(from_primitive(item, ProviderSymbolMapping))
        for item in value["references"]:
            catalog.add_reference(from_primitive(item, InstrumentReference))
        return catalog


def _load_many(repository, values, target) -> None:
    for item in values:
        repository.add(from_primitive(item, target))


def instrument_to_primitive(item: InstrumentDefinition) -> dict:
    value = to_primitive(item)
    value["contract_spec_type"] = type(item.contract_spec).__name__
    return value


def instrument_from_primitive(item: dict) -> InstrumentDefinition:
    spec_types = {
        "IndexSpec": IndexSpec, "EquitySpec": EquitySpec,
        "ListedOptionSpec": ListedOptionSpec, "CryptoSpotSpec": CryptoSpotSpec,
        "FutureSpec": FutureSpec, "PerpetualSpec": PerpetualSpec,
        "CryptoOptionSpec": CryptoOptionSpec, "TokenizedEquitySpec": TokenizedEquitySpec,
    }
    try:
        spec_type = spec_types[item["contract_spec_type"]]
    except KeyError as error:
        raise ValueError(f"unsupported contract spec type: {item.get('contract_spec_type')}") from error
    decoded = dict(item)
    decoded.pop("contract_spec_type")
    decoded["contract_spec"] = from_primitive(decoded["contract_spec"], spec_type)
    return from_primitive(decoded, InstrumentDefinition)
