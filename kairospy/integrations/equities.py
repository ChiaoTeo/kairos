from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from kairospy.core.reference import (
    Asset,
    AssetId,
    AssetType,
    Entity,
    EntityId,
    EntityType,
    InstrumentDefinition,
    InstrumentId,
    InstrumentType,
    ListingDefinition,
    ListingId,
    MarketDefinition,
    MarketId,
    MarketStatus,
    ReferenceCatalog,
)
from kairospy.core.reference.identity import reference_slug

from .instruments import ReferenceSnapshot
from .protocols import InstrumentProvider


@dataclass(frozen=True, slots=True)
class EquityReferenceSnapshotProvider:
    provider: InstrumentProvider

    def reference_snapshot(
        self,
        *,
        as_of: datetime,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceSnapshot:
        catalog = catalog_from_equity_rows(self.provider.fetch_markets(params=params), effective_from=as_of)
        return ReferenceSnapshot(catalog, as_of)


def catalog_from_equity_rows(rows: Iterable[Mapping[str, object]], *, effective_from: datetime) -> ReferenceCatalog:
    catalog = ReferenceCatalog()
    seen_entities: set[str] = set()
    seen_assets: set[str] = set()
    seen_instruments: set[str] = set()
    seen_listings: set[str] = set()
    seen_markets: set[str] = set()
    for row in rows:
        venue = _required_text(row.get("venue"), "venue")
        ticker = _required_text(row.get("ticker") or row.get("source_symbol"), "ticker")
        name = _optional_text(row.get("name")) or ticker
        stable_key = _stable_key(row, ticker)
        entity_id = EntityId(f"entity:company:{stable_key}")
        asset_id = AssetId(f"asset:equity:{stable_key}")
        instrument_id = InstrumentId(f"instrument:equity:{stable_key}")
        listing_id = ListingId(f"listing:{reference_slug(venue)}:{stable_key}")
        market_id = MarketId(f"market:{reference_slug(venue)}:equity:{stable_key}")
        currency = _optional_text(row.get("currency")) or "USD"
        currency_asset_id = AssetId(f"asset:fiat:{reference_slug(currency)}")

        if str(entity_id) not in seen_entities:
            catalog.add_entity(Entity(entity_id, EntityType.COMPANY, name, effective_from=effective_from, metadata=_metadata(row)))
            seen_entities.add(str(entity_id))
        if str(asset_id) not in seen_assets:
            catalog.add_asset(
                Asset(
                    asset_id,
                    AssetType.EQUITY,
                    ticker,
                    name=name,
                    issuer_id=entity_id,
                    effective_from=effective_from,
                    metadata=_metadata(row),
                )
            )
            seen_assets.add(str(asset_id))
        if str(currency_asset_id) not in seen_assets:
            catalog.add_asset(Asset(currency_asset_id, AssetType.FIAT, currency, effective_from=effective_from))
            seen_assets.add(str(currency_asset_id))
        if str(instrument_id) not in seen_instruments:
            catalog.add_instrument(
                InstrumentDefinition(
                    instrument_id,
                    InstrumentType.EQUITY,
                    base_asset_id=asset_id,
                    quote_asset_id=currency_asset_id,
                    display_name=f"{ticker} common stock",
                    effective_from=effective_from,
                    metadata=_metadata(row),
                )
            )
            seen_instruments.add(str(instrument_id))
        status = _market_status(row)
        if str(listing_id) not in seen_listings:
            catalog.add_listing(
                ListingDefinition(
                    listing_id,
                    instrument_id,
                    venue,
                    ticker,
                    venue_instrument_id=_optional_text(row.get("venue_instrument_id") or row.get("figi") or row.get("composite_figi")),
                    currency_asset_id=currency_asset_id,
                    status=status,
                    effective_from=effective_from,
                    metadata=_metadata(row),
                )
            )
            seen_listings.add(str(listing_id))
        if str(market_id) not in seen_markets:
            catalog.add_market(
                MarketDefinition(
                    market_id,
                    instrument_id,
                    listing_id,
                    venue,
                    "equity",
                    ticker,
                    status=status,
                    price_tick=_optional_decimal(row.get("price_tick")),
                    amount_tick=_optional_decimal(row.get("amount_tick")),
                    min_amount=_optional_decimal(row.get("min_amount")),
                    min_notional=_optional_decimal(row.get("min_notional")),
                    effective_from=effective_from,
                    metadata=_metadata(row),
                )
            )
            seen_markets.add(str(market_id))
    return catalog


def _stable_key(row: Mapping[str, object], ticker: str) -> str:
    for key in ("instrument_id", "asset_id", "entity_id", "cik", "figi", "composite_figi", "share_class_figi"):
        value = _optional_text(row.get(key))
        if value:
            return reference_slug(value.removeprefix("instrument:").removeprefix("asset:").removeprefix("entity:"))
    return reference_slug(ticker)


def _market_status(row: Mapping[str, object]) -> MarketStatus:
    active = row.get("active")
    if active is True:
        return MarketStatus.ACTIVE
    if active is False:
        return MarketStatus.DELISTED
    text = str(row.get("status") or "").strip().lower()
    return {
        "active": MarketStatus.ACTIVE,
        "listed": MarketStatus.ACTIVE,
        "trading": MarketStatus.ACTIVE,
        "halted": MarketStatus.HALTED,
        "delisted": MarketStatus.DELISTED,
    }.get(text, MarketStatus.UNKNOWN)


def _metadata(row: Mapping[str, object]) -> Mapping[str, object]:
    return {str(key): value for key, value in row.items() if key not in {"active"}}


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} cannot be empty")
    return text


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _optional_decimal(value: object) -> Decimal | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


__all__ = ["EquityReferenceSnapshotProvider", "catalog_from_equity_rows"]
