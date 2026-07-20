from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from kairos.domain.identity import AssetId, InstrumentId, VenueId
from kairos.domain.product import ListedOptionSpec, ProductType, SettlementType
from kairos.reference import (
    AssetDefinition, AssetType, ListingDefinition, ListingId, ReferenceCatalog,
    TradingRules, VenueDefinition, VenueType,
)
from kairos.reference.factory import publish_instrument


def publish_test_instrument(
    catalog: ReferenceCatalog,
    instrument_id: InstrumentId,
    instrument_type: ProductType,
    display_name: str,
    contract_spec,
    trading_currency: AssetId,
    venue_id: VenueId,
    symbol: str,
    effective_from: datetime,
    effective_to: datetime | None = None,
    *,
    price_increment: Decimal = Decimal("0.01"),
    quantity_increment: Decimal = Decimal("1"),
    minimum_quantity: Decimal = Decimal("1"),
    minimum_notional: Decimal | None = None,
):
    asset_ids = {trading_currency}
    for name in (
        "base_asset", "quote_asset", "underlying_asset", "settlement_asset",
        "premium_asset", "token_asset", "trading_currency", "index_currency",
    ):
        value = getattr(contract_spec, name, None)
        if isinstance(value, AssetId):
            asset_ids.add(value)
    deliverable_asset = None
    if isinstance(contract_spec, ListedOptionSpec) and contract_spec.settlement_type is SettlementType.PHYSICAL:
        underlying = catalog.instruments.get(contract_spec.underlying, effective_from)
        if not underlying.display_name:
            raise ValueError("physical-option test fixture requires an explicit underlying display name")
        deliverable_asset = AssetId(underlying.display_name)
        asset_ids.add(deliverable_asset)
    fiat = {"USD", "EUR", "GBP", "JPY", "CNY", "CHF", "CAD", "AUD", "HKD"}
    assets = tuple(
        AssetDefinition(
            asset_id,
            AssetType.SECURITY if asset_id == deliverable_asset else AssetType.FIAT if asset_id.value in fiat else AssetType.CRYPTO,
            asset_id.value,
            effective_from,
            decimals=2 if asset_id.value in fiat else 8,
        )
        for asset_id in sorted(asset_ids, key=lambda item: item.value)
        if not any(item.asset_id == asset_id for item in catalog.assets.values())
    )
    venue = VenueDefinition(
        venue_id,
        VenueType.CRYPTO_EXCHANGE if instrument_type in {
            ProductType.CRYPTO_SPOT, ProductType.CRYPTO_OPTION, ProductType.PERPETUAL,
        } else VenueType.EXCHANGE,
        venue_id.value,
        "UTC", effective_from,
        mic=venue_id.value.upper() if len(venue_id.value) == 4 and venue_id.value.startswith("x") else None,
    )
    return publish_instrument(
        catalog,
        instrument_id=instrument_id,
        instrument_type=instrument_type,
        display_name=display_name,
        contract_spec=contract_spec,
        trading_currency=trading_currency,
        listings=(ListingDefinition(
            ListingId(f"listing:{venue_id.value}:{instrument_id.value}"), instrument_id, venue_id, symbol,
            trading_currency, TradingRules(price_increment, quantity_increment, minimum_quantity, minimum_notional=minimum_notional),
            effective_from, effective_to, symbol,
        ),),
        effective_from=effective_from,
        effective_to=effective_to,
        asset_definitions=assets,
        venue_definitions=() if any(item.venue_id == venue_id for item in catalog.venues.values()) else (venue,),
        physical_deliverable_asset=deliverable_asset,
    )
