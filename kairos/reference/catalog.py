from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from typing import Callable, Generic, Iterable, TypeVar

from kairos.domain.identity import AccountKey, InstrumentId

from .identity import BenchmarkId, ListingId, ProductId
from .contracts import (
    AssetDefinition, BenchmarkDefinition, ContractSeries, EconomicProduct,
    EntityDefinition, ExecutionRoute, InstrumentDefinition,
    InstrumentReference, ListingDefinition, MappingTargetType,
    ProviderSymbolMapping, ReferenceRole,
)

T = TypeVar("T")
K = TypeVar("K")


class VersionedRepository(Generic[K, T]):
    def __init__(self, key: Callable[[T], K]) -> None:
        self._key = key
        self._values: dict[K, list[T]] = defaultdict(list)

    def add(self, value: T) -> None:
        key = self._key(value)
        versions = self._values[key]
        if value in versions:
            return
        start, end = value.effective_from, value.effective_to  # type: ignore[attr-defined]
        if any(_overlaps(start, end, item.effective_from, item.effective_to) for item in versions):  # type: ignore[attr-defined]
            raise ValueError(f"overlapping reference definition: {key}")
        versions.append(value)
        versions.sort(key=lambda item: item.effective_from)  # type: ignore[attr-defined]

    def get(self, key: K, at: datetime) -> T:
        matches = [item for item in self._values.get(key, ()) if item.active_at(at)]  # type: ignore[attr-defined]
        if len(matches) != 1:
            raise LookupError(f"reference definition not found or ambiguous: {key} at {at}")
        return matches[0]

    def values(self, at: datetime | None = None) -> tuple[T, ...]:
        result = [item for versions in self._values.values() for item in versions]
        if at is not None:
            result = [item for item in result if item.active_at(at)]  # type: ignore[attr-defined]
        return tuple(result)

    def supersede(self, value: T, effective_at: datetime) -> None:
        key = self._key(value)
        current = self.get(key, effective_at)
        if value.effective_from != effective_at:  # type: ignore[attr-defined]
            raise ValueError("replacement definition must start at effective_at")
        versions = self._values[key]
        versions[versions.index(current)] = replace(current, effective_to=effective_at)
        self.add(value)

    def end(self, key: K, effective_at: datetime) -> None:
        current = self.get(key, effective_at)
        if effective_at <= current.effective_from:  # type: ignore[attr-defined]
            raise ValueError("end time must be after effective_from")
        versions = self._values[key]
        versions[versions.index(current)] = replace(current, effective_to=effective_at)


class ReferenceCatalog:
    def __init__(self) -> None:
        self.assets = VersionedRepository(lambda item: item.asset_id)
        self.entities = VersionedRepository(lambda item: item.entity_id)
        self.venues = VersionedRepository(lambda item: item.venue_id)
        self.benchmarks = VersionedRepository(lambda item: item.benchmark_id)
        self.products = VersionedRepository(lambda item: item.product_id)
        self.series = VersionedRepository(lambda item: item.series_id)
        self.instruments = VersionedRepository(lambda item: item.instrument_id)
        self.listings = VersionedRepository(lambda item: item.listing_id)
        self.routes = VersionedRepository(lambda item: item.route_id)
        self.networks = VersionedRepository(lambda item: item.network_id)
        self.network_assets = VersionedRepository(lambda item: item.network_asset_id)
        self.rails = VersionedRepository(lambda item: item.rail_id)
        self.locations = VersionedRepository(lambda item: item.location_id)
        self.settlements = VersionedRepository(lambda item: item.settlement_terms_id)
        self._mappings: list[ProviderSymbolMapping] = []
        self._references: list[InstrumentReference] = []

    def merge(self, source: "ReferenceCatalog") -> None:
        for name in (
            "assets", "entities", "venues", "benchmarks", "products", "series", "instruments", "listings",
            "routes", "networks", "network_assets", "rails", "locations", "settlements",
        ):
            destination = getattr(self, name)
            for value in getattr(source, name).values():
                try:
                    destination.add(value)
                except ValueError as error:
                    if name not in {"assets", "venues", "benchmarks"} or "overlapping reference definition" not in str(error):
                        raise
        for mapping in source.mappings():
            if mapping not in self._mappings:
                self.add_mapping(mapping)
        for reference in source.all_references():
            self.add_reference(reference)

    def add_mapping(self, mapping: ProviderSymbolMapping) -> None:
        same_key = lambda item: (
            item.provider_id == mapping.provider_id and item.namespace == mapping.namespace
            and item.external_id == mapping.external_id and item.publisher_id == mapping.publisher_id
        )
        if mapping in self._mappings:
            raise ValueError(
                f"duplicate provider mapping: {mapping.provider_id}/{mapping.namespace}/{mapping.external_id}",
            )
        if any(same_key(item) and _overlaps(item.effective_from, item.effective_to, mapping.effective_from, mapping.effective_to) for item in self._mappings):
            raise ValueError(f"overlapping provider mapping: {mapping.provider_id}/{mapping.namespace}/{mapping.external_id}")
        self._mappings.append(mapping)

    def resolve_provider_symbol(self, provider_id, namespace: str, external_id: str, at: datetime, *, publisher_id: str | None = None) -> ProviderSymbolMapping:
        matches = [item for item in self._mappings if item.provider_id == provider_id and item.namespace == namespace and item.external_id == external_id and item.publisher_id == publisher_id and item.active_at(at)]
        if len(matches) != 1:
            raise LookupError(f"provider symbol not found or ambiguous: {provider_id}/{namespace}/{external_id} at {at}")
        return matches[0]

    def mappings(self) -> tuple[ProviderSymbolMapping, ...]:
        return tuple(self._mappings)

    def add_reference(self, reference: InstrumentReference) -> None:
        if reference in self._references:
            return
        for item in self._references:
            same = item.source_instrument_id == reference.source_instrument_id and item.role == reference.role and item.target == reference.target
            if same and _overlaps(item.effective_from, item.effective_to, reference.effective_from, reference.effective_to):
                raise ValueError("overlapping instrument reference")
        self._references.append(reference)

    def references(self, instrument_id: InstrumentId, role: ReferenceRole, at: datetime) -> tuple[InstrumentReference, ...]:
        return tuple(item for item in self._references if item.source_instrument_id == instrument_id and item.role is role and item.active_at(at))

    def all_references(self) -> tuple[InstrumentReference, ...]:
        return tuple(self._references)

    def active_listings(self, instrument_id: InstrumentId, at: datetime) -> tuple[ListingDefinition, ...]:
        return tuple(item for item in self.listings.values(at) if item.instrument_id == instrument_id)

    def resolve_execution_route(self, account: AccountKey, instrument_id: InstrumentId, at: datetime) -> ExecutionRoute:
        listing_ids = {item.listing_id for item in self.active_listings(instrument_id, at)}
        matches = [item for item in self.routes.values(at) if item.account_key == account and item.listing_id in listing_ids]
        if len(matches) != 1:
            raise LookupError(f"execution route not found or ambiguous: {account.value}/{instrument_id} at {at}")
        return matches[0]

    def validate_integrity(self, at: datetime) -> tuple[str, ...]:
        issues: list[str] = []
        entity_ids = {item.entity_id for item in self.entities.values(at)}
        product_ids = {item.product_id for item in self.products.values(at)}
        products = {item.product_id: item for item in self.products.values(at)}
        series_ids = {item.series_id for item in self.series.values(at)}
        series = {item.series_id: item for item in self.series.values(at)}
        instrument_ids = {item.instrument_id for item in self.instruments.values(at)}
        listing_ids = {item.listing_id for item in self.listings.values(at)}
        benchmark_ids = {item.benchmark_id for item in self.benchmarks.values(at)}
        asset_ids = {item.asset_id for item in self.assets.values(at)}
        venue_ids = {item.venue_id for item in self.venues.values(at)}
        network_ids = {item.network_id for item in self.networks.values(at)}
        for item in self.assets.values(at):
            if item.issuer_id is not None and item.issuer_id not in entity_ids:
                issues.append(f"asset_missing_issuer:{item.asset_id}:{item.issuer_id}")
        for item in self.benchmarks.values(at):
            if item.currency not in asset_ids:
                issues.append(f"benchmark_missing_currency:{item.benchmark_id}:{item.currency}")
            if item.administrator_id is not None and item.administrator_id not in entity_ids:
                issues.append(f"benchmark_missing_administrator:{item.benchmark_id}:{item.administrator_id}")
        for item in self.products.values(at):
            if item.currency is not None and item.currency not in asset_ids:
                issues.append(f"product_missing_currency:{item.product_id}:{item.currency}")
            if item.issuer_id is not None and item.issuer_id not in entity_ids:
                issues.append(f"product_missing_issuer:{item.product_id}:{item.issuer_id}")
        for item in self.series.values(at):
            if item.product_id not in product_ids:
                issues.append(f"series_missing_product:{item.series_id}:{item.product_id}")
        for item in self.instruments.values(at):
            if item.product_id not in product_ids:
                issues.append(f"instrument_missing_product:{item.instrument_id}:{item.product_id}")
            elif products[item.product_id].product_type is not item.instrument_type:
                issues.append(f"instrument_product_type_mismatch:{item.instrument_id}:{item.product_id}")
            if item.series_id is not None:
                if item.series_id not in series_ids:
                    issues.append(f"instrument_missing_series:{item.instrument_id}:{item.series_id}")
                elif series[item.series_id].product_id != item.product_id:
                    issues.append(f"instrument_series_product_mismatch:{item.instrument_id}:{item.series_id}")
            if item.settlement_terms_id is not None:
                try:
                    self.settlements.get(item.settlement_terms_id, at)
                except LookupError:
                    issues.append(f"instrument_missing_settlement_terms:{item.instrument_id}:{item.settlement_terms_id}")
        for item in self.listings.values(at):
            if item.instrument_id not in instrument_ids:
                issues.append(f"listing_missing_instrument:{item.listing_id}:{item.instrument_id}")
            if item.venue_id not in venue_ids:
                issues.append(f"listing_missing_venue:{item.listing_id}:{item.venue_id}")
            if item.trading_currency not in asset_ids:
                issues.append(f"listing_missing_currency:{item.listing_id}:{item.trading_currency}")
        for item in self.routes.values(at):
            if item.listing_id not in listing_ids:
                issues.append(f"route_missing_listing:{item.route_id}:{item.listing_id}")
            if item.broker_id.value != item.account_key.institution_id.value:
                issues.append(f"route_broker_account_mismatch:{item.route_id}:{item.broker_id}:{item.account_key.institution_id}")
        for item in self._references:
            if not item.active_at(at):
                continue
            target = item.target
            if item.source_instrument_id not in instrument_ids:
                issues.append(f"reference_missing_source:{item.source_instrument_id}")
            if target.instrument_id is not None and target.instrument_id not in instrument_ids:
                issues.append(f"reference_missing_instrument:{target.instrument_id}")
            if target.product_id is not None and target.product_id not in product_ids:
                issues.append(f"reference_missing_product:{target.product_id}")
            if target.benchmark_id is not None and target.benchmark_id not in benchmark_ids:
                issues.append(f"reference_missing_benchmark:{target.benchmark_id}")
            if target.asset_id is not None and target.asset_id not in asset_ids:
                issues.append(f"reference_missing_asset:{target.asset_id}")
        for item in self.settlements.values(at):
            terms = item.terms
            if terms.settlement_asset is not None and terms.settlement_asset not in asset_ids:
                issues.append(f"settlement_missing_asset:{item.settlement_terms_id}:{terms.settlement_asset}")
            for deliverable in terms.deliverables:
                if deliverable.asset_id not in asset_ids:
                    issues.append(f"settlement_missing_deliverable:{item.settlement_terms_id}:{deliverable.asset_id}")
            if terms.benchmark_id is not None and terms.benchmark_id not in benchmark_ids:
                issues.append(f"settlement_missing_benchmark:{item.settlement_terms_id}:{terms.benchmark_id}")
        target_ids = {
            MappingTargetType.PRODUCT: {item.value for item in product_ids},
            MappingTargetType.INSTRUMENT: {item.value for item in instrument_ids},
            MappingTargetType.LISTING: {item.value for item in listing_ids},
            MappingTargetType.BENCHMARK: {item.value for item in benchmark_ids},
            MappingTargetType.SERIES: {item.value for item in series_ids},
        }
        for item in self._mappings:
            if item.active_at(at) and item.target_id not in target_ids[item.target_type]:
                issues.append(f"mapping_missing_target:{item.provider_id}:{item.external_id}:{item.target_id}")
        for item in self.networks.values(at):
            if item.native_asset is not None and item.native_asset not in asset_ids:
                issues.append(f"network_missing_native_asset:{item.network_id}:{item.native_asset}")
        for item in self.network_assets.values(at):
            if item.network_id not in network_ids:
                issues.append(f"network_asset_missing_network:{item.network_asset_id}:{item.network_id}")
            if item.asset_id not in asset_ids:
                issues.append(f"network_asset_missing_asset:{item.network_asset_id}:{item.asset_id}")
        for item in self.rails.values(at):
            if item.network_id is not None and item.network_id not in network_ids:
                issues.append(f"rail_missing_network:{item.rail_id}:{item.network_id}")
            for asset_id in item.supported_assets:
                if asset_id not in asset_ids:
                    issues.append(f"rail_missing_asset:{item.rail_id}:{asset_id}")
        for item in self.locations.values(at):
            if item.network_id is not None and item.network_id not in network_ids:
                issues.append(f"location_missing_network:{item.location_id}:{item.network_id}")
        return tuple(sorted(issues))

    def network_asset(self, asset_id, network_id, at: datetime):
        matches = [item for item in self.network_assets.values(at) if item.asset_id == asset_id and item.network_id == network_id]
        if len(matches) != 1:
            raise LookupError(f"network asset not found or ambiguous: {asset_id}/{network_id} at {at}")
        return matches[0]

    def transfer_rails(self, asset_id, at: datetime):
        return tuple(item for item in self.rails.values(at) if asset_id in item.supported_assets)


def _overlaps(start_a, end_a, start_b, end_b) -> bool:
    latest_start = max(start_a, start_b)
    if end_a is None and end_b is None:
        return True
    earliest_end = end_b if end_a is None else end_a if end_b is None else min(end_a, end_b)
    return latest_start < earliest_end
