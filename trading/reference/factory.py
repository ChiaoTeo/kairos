from __future__ import annotations

from datetime import datetime

from trading.domain.identity import AssetId, InstrumentId
from trading.domain.product import (
    CryptoOptionSpec, FutureSpec, ListedOptionSpec, PerpetualSpec, ProductType,
    SettlementSession, SettlementType, TokenizedEquitySpec,
)

from .catalog import ReferenceCatalog
from .identity import BenchmarkId, ProductId, SeriesId
from .models import (
    AssetDefinition, BenchmarkDefinition, BenchmarkType, ContractSeries,
    Deliverable, EconomicProduct, InstrumentDefinition, InstrumentLifecycle,
    InstrumentReference, ListingDefinition, ReferenceRole, ReferenceTarget,
    SettlementMethod, SettlementTerms, SettlementTermsDefinition, VenueDefinition,
)


def publish_instrument(
    catalog: ReferenceCatalog,
    *,
    instrument_id: InstrumentId,
    instrument_type: ProductType,
    display_name: str,
    contract_spec,
    trading_currency: AssetId,
    listings: tuple[ListingDefinition, ...],
    effective_from: datetime,
    effective_to: datetime | None = None,
    product_id: ProductId | None = None,
    trading_class: str | None = None,
    asset_definitions: tuple[AssetDefinition, ...] = (),
    venue_definitions: tuple[VenueDefinition, ...] = (),
    physical_deliverable_asset: AssetId | None = None,
) -> InstrumentDefinition:
    """Publish one current instrument and its normalized reference facts."""
    product_id = product_id or product_id_for(instrument_id, contract_spec, trading_class=trading_class)
    for asset in asset_definitions:
        existing_assets = tuple(item for item in catalog.assets.values() if item.asset_id == asset.asset_id)
        if existing_assets and any(item.asset_type is not asset.asset_type for item in existing_assets):
            raise ValueError(f"conflicting authoritative asset type: {asset.asset_id}")
        if not existing_assets:
            catalog.assets.add(asset)
    for venue in venue_definitions:
        existing_venues = tuple(item for item in catalog.venues.values() if item.venue_id == venue.venue_id)
        if existing_venues and any(
            item.venue_type is not venue.venue_type or item.timezone != venue.timezone
            for item in existing_venues
        ):
            raise ValueError(f"conflicting authoritative venue definition: {venue.venue_id}")
        if not existing_venues:
            catalog.venues.add(venue)
    _require_publishing_references(catalog, trading_currency, listings, contract_spec, effective_from)
    _add_product(catalog, product_id, instrument_type, display_name, trading_currency, effective_from, effective_to)
    for listing in listings:
        catalog.listings.add(listing)
    series_id = _add_series(catalog, product_id, trading_class, contract_spec, effective_from, effective_to)
    settlement_id = _add_settlement(
        catalog, instrument_id, instrument_type, contract_spec, trading_currency, effective_from, effective_to,
        physical_deliverable_asset,
    )
    definition = InstrumentDefinition(
        instrument_id, product_id, instrument_type, contract_spec,
        _lifecycle(contract_spec, effective_from), effective_from, effective_to,
        series_id, display_name, settlement_id,
    )
    catalog.instruments.add(definition)
    add_instrument_references(catalog, definition)
    return definition


def product_id_for(instrument_id: InstrumentId, spec, *, trading_class: str | None = None) -> ProductId:
    if isinstance(spec, ListedOptionSpec):
        family = f":{trading_class.strip().lower()}" if trading_class and trading_class.strip() else ""
        return ProductId(f"product:listed-option:{spec.underlying.value}{family}")
    if isinstance(spec, CryptoOptionSpec):
        return ProductId(f"product:crypto-option:{spec.underlying_asset.value}:{spec.settlement_index}")
    if isinstance(spec, FutureSpec):
        return ProductId(f"product:future:{spec.underlying_asset.value}:{spec.settlement_index}")
    if isinstance(spec, PerpetualSpec):
        return ProductId(f"product:perpetual:{spec.underlying_asset.value}:{spec.index_id}")
    if isinstance(spec, TokenizedEquitySpec):
        return ProductId(f"product:tokenized-equity:{spec.reference_equity.value}")
    return ProductId(f"product:{instrument_id.value}")


def _add_product(catalog, product_id, product_type, name, currency, start, end) -> None:
    existing = tuple(item for item in catalog.products.values() if item.product_id == product_id)
    if existing:
        if any(item.product_type is not product_type or item.currency != currency for item in existing):
            raise ValueError(f"conflicting economic product definition: {product_id}")
        return
    catalog.products.add(EconomicProduct(product_id, product_type, name, start, end, currency=currency))


def _add_series(catalog, product_id, trading_class, spec, start, end):
    expiry = getattr(spec, "expiry", None)
    if expiry is None:
        return None
    family = trading_class.strip().lower() if trading_class and trading_class.strip() else "default"
    series_id = SeriesId(f"series:{product_id.value}:{family}:{expiry.isoformat()}")
    if not any(item.series_id == series_id for item in catalog.series.values()):
        catalog.series.add(ContractSeries(series_id, product_id, start, end, expiry, trading_class))
    return series_id


def _lifecycle(spec, effective_from) -> InstrumentLifecycle:
    expiry = getattr(spec, "expiry", None)
    return InstrumentLifecycle(effective_from, getattr(spec, "last_trade_at", None), expiry, expiry)


def _require_publishing_references(catalog, currency, listings, spec, at) -> None:
    required_assets = {currency}
    for name in (
        "base_asset", "quote_asset", "underlying_asset", "settlement_asset", "premium_asset",
        "token_asset", "trading_currency", "index_currency",
    ):
        value = getattr(spec, name, None)
        if isinstance(value, AssetId):
            required_assets.add(value)
    known_assets = {item.asset_id for item in catalog.assets.values(at)}
    missing_assets = sorted(required_assets - known_assets, key=lambda item: item.value)
    if missing_assets:
        raise ValueError(f"instrument publishing requires authoritative assets: {missing_assets}")
    known_venues = {item.venue_id for item in catalog.venues.values(at)}
    missing_venues = sorted({item.venue_id for item in listings} - known_venues, key=lambda item: item.value)
    if missing_venues:
        raise ValueError(f"instrument publishing requires authoritative venues: {missing_venues}")


def add_instrument_references(catalog: ReferenceCatalog, definition: InstrumentDefinition) -> None:
    spec = definition.contract_spec
    if isinstance(spec, ListedOptionSpec):
        for role in (ReferenceRole.ECONOMIC_UNDERLYING, ReferenceRole.PRICING_UNDERLYING):
            catalog.add_reference(InstrumentReference(
                definition.instrument_id, role, ReferenceTarget(instrument_id=spec.underlying),
                definition.effective_from, definition.effective_to,
            ))
    elif isinstance(spec, (FutureSpec, PerpetualSpec, CryptoOptionSpec)):
        catalog.add_reference(InstrumentReference(
            definition.instrument_id, ReferenceRole.ECONOMIC_UNDERLYING,
            ReferenceTarget(asset_id=spec.underlying_asset), definition.effective_from, definition.effective_to,
        ))
    elif isinstance(spec, TokenizedEquitySpec):
        catalog.add_reference(InstrumentReference(
            definition.instrument_id, ReferenceRole.REFERENCE_INSTRUMENT,
            ReferenceTarget(instrument_id=spec.reference_equity), definition.effective_from, definition.effective_to,
        ))


def _add_settlement(catalog, instrument_id, instrument_type, spec, currency, start, end, physical_deliverable_asset):
    if not isinstance(spec, (ListedOptionSpec, CryptoOptionSpec, FutureSpec)):
        return None
    settlement_id = f"settlement:{instrument_id.value}"
    if isinstance(spec, ListedOptionSpec) and spec.settlement_type is SettlementType.PHYSICAL:
        if physical_deliverable_asset is None:
            raise ValueError("physical settlement requires an explicit deliverable asset")
        catalog.assets.get(physical_deliverable_asset, start)
        terms = SettlementTerms(
            SettlementMethod.PHYSICAL, spec.settlement_session, settlement_asset=currency,
            determination_at=spec.expiry, settlement_at=spec.expiry,
            deliverables=(Deliverable(physical_deliverable_asset, spec.multiplier),),
        )
    else:
        index = spec.underlying.value if isinstance(spec, ListedOptionSpec) else spec.settlement_index
        settlement_asset = currency if isinstance(spec, ListedOptionSpec) else spec.settlement_asset
        benchmark_id = BenchmarkId(f"settlement:{index}")
        if not any(item.benchmark_id == benchmark_id for item in catalog.benchmarks.values()):
            catalog.benchmarks.add(BenchmarkDefinition(
                benchmark_id, BenchmarkType.SETTLEMENT_VALUE, index, settlement_asset, start,
            ))
        expiry = getattr(spec, "expiry", None)
        terms = SettlementTerms(
            SettlementMethod.CASH,
            spec.settlement_session if isinstance(spec, ListedOptionSpec) else SettlementSession.CONTINUOUS,
            settlement_asset, benchmark_id, expiry, expiry,
        )
    catalog.settlements.add(SettlementTermsDefinition(settlement_id, terms, start, end))
    return settlement_id
