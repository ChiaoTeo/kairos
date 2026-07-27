from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from kairospy.reference import (
    Asset,
    AssetId,
    AssetType,
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
from kairospy.reference.identity import reference_slug

from .protocols import InstrumentProvider


@dataclass(frozen=True, slots=True)
class InstrumentReferenceSnapshotProvider:
    provider: InstrumentProvider

    def reference_snapshot(
        self,
        *,
        as_of: datetime,
        params: Mapping[str, object] | None = None,
    ) -> "ReferenceSnapshot":
        catalog = catalog_from_market_rows(tuple(self.provider.fetch_markets(params=params)), effective_from=as_of)
        return ReferenceSnapshot(catalog, as_of)


@dataclass(frozen=True, slots=True)
class ReferenceSnapshot:
    catalog: ReferenceCatalog
    as_of: datetime

    def resolve_market(
        self,
        source_symbol: str,
        *,
        venue: str,
        market: str | None = None,
        at: datetime | None = None,
    ) -> MarketDefinition:
        return self.catalog.resolve_market(source_symbol, venue=venue, market=market, at=at or self.as_of)

    def list_markets(
        self,
        *,
        venue: str | None = None,
        market: str | None = None,
        status: MarketStatus | str | None = None,
        active_only: bool = False,
        at: datetime | None = None,
    ) -> tuple[MarketDefinition, ...]:
        return self.catalog.list_markets(
            at=at or self.as_of,
            venue=venue,
            market=market,
            status=status,
            active_only=active_only,
        )


def catalog_from_market_rows(rows: Iterable[Mapping[str, object]], *, effective_from: datetime) -> ReferenceCatalog:
    catalog = ReferenceCatalog()
    seen_assets: set[str] = set()
    seen_instruments: set[str] = set()
    seen_listings: set[str] = set()
    seen_markets: set[str] = set()
    for row in rows:
        base = _optional_text(row.get("base"))
        quote = _optional_text(row.get("quote"))
        base_asset_id = _asset_id(base) if base else None
        quote_asset_id = _asset_id(quote) if quote else None
        for symbol, asset_id in ((base, base_asset_id), (quote, quote_asset_id)):
            if symbol and asset_id and str(asset_id) not in seen_assets:
                catalog.add_asset(Asset(asset_id, AssetType.CRYPTO, symbol, effective_from=effective_from))
                seen_assets.add(str(asset_id))

        instrument_type = _instrument_type(row.get("market"))
        instrument_id = _instrument_id(instrument_type, base, quote, row)
        if str(instrument_id) not in seen_instruments:
            catalog.add_instrument(
                InstrumentDefinition(
                    instrument_id,
                    instrument_type,
                    base_asset_id=base_asset_id,
                    quote_asset_id=quote_asset_id,
                    display_name=_display_name(base, quote, row),
                    effective_from=effective_from,
                )
            )
            seen_instruments.add(str(instrument_id))

        venue = _required_text(row.get("venue"), "venue")
        market = _required_text(row.get("market"), "market")
        source_symbol = _required_text(row.get("source_symbol"), "source_symbol")
        listing_id = ListingId(f"listing:{reference_slug(venue)}:{reference_slug(market)}:{reference_slug(source_symbol)}")
        if str(listing_id) not in seen_listings:
            catalog.add_listing(
                ListingDefinition(
                    listing_id,
                    instrument_id,
                    venue,
                    source_symbol,
                    venue_instrument_id=_optional_text(row.get("venue_instrument_id")),
                    currency_asset_id=quote_asset_id,
                    status=_market_status(row),
                    effective_from=effective_from,
                )
            )
            seen_listings.add(str(listing_id))

        market_id = MarketId(f"market:{reference_slug(venue)}:{reference_slug(market)}:{reference_slug(source_symbol)}")
        if str(market_id) not in seen_markets:
            catalog.add_market(
                MarketDefinition(
                    market_id,
                    instrument_id,
                    listing_id,
                    venue,
                    market,
                    source_symbol,
                    status=_market_status(row),
                    price_precision=_optional_int(row.get("price_precision")),
                    amount_precision=_optional_int(row.get("amount_precision")),
                    min_amount=_optional_decimal(row.get("min_amount")),
                    min_notional=_optional_decimal(row.get("min_notional")),
                    contract_size=_optional_decimal(row.get("contract_size")),
                    metadata={"raw": row.get("raw")} if row.get("raw") is not None else {},
                    effective_from=effective_from,
                )
            )
            seen_markets.add(str(market_id))
    return catalog


def market_definitions_from_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    effective_from: datetime,
) -> tuple[MarketDefinition, ...]:
    return catalog_from_market_rows(rows, effective_from=effective_from).list_markets(at=effective_from)


def _asset_id(symbol: str) -> AssetId:
    return AssetId(f"asset:crypto:{reference_slug(symbol)}")


def _instrument_id(instrument_type: InstrumentType, base: str | None, quote: str | None, row: Mapping[str, object]) -> InstrumentId:
    if base and quote:
        return InstrumentId(f"instrument:{instrument_type.value}:{reference_slug(base)}:{reference_slug(quote)}")
    return InstrumentId(
        "instrument:"
        f"{instrument_type.value}:{reference_slug(row.get('venue'))}:{reference_slug(row.get('market'))}:{reference_slug(row.get('source_symbol'))}"
    )


def _display_name(base: str | None, quote: str | None, row: Mapping[str, object]) -> str:
    if base and quote:
        return f"{base}/{quote}"
    return _required_text(row.get("source_symbol"), "source_symbol")


def _instrument_type(value: object) -> InstrumentType:
    text = str(value or "").strip().lower()
    return {
        "spot": InstrumentType.SPOT,
        "swap": InstrumentType.PERPETUAL,
        "perp": InstrumentType.PERPETUAL,
        "perpetual": InstrumentType.PERPETUAL,
        "future": InstrumentType.FUTURE,
        "futures": InstrumentType.FUTURE,
        "option": InstrumentType.OPTION,
        "options": InstrumentType.OPTION,
        "equity": InstrumentType.EQUITY,
    }.get(text, InstrumentType.OTHER)


def _market_status(row: Mapping[str, object]) -> MarketStatus:
    active = row.get("active")
    if active is True:
        return MarketStatus.ACTIVE
    if active is False:
        return MarketStatus.DELISTED
    text = str(row.get("status") or "").strip().lower()
    return {
        "trading": MarketStatus.ACTIVE,
        "active": MarketStatus.ACTIVE,
        "halted": MarketStatus.HALTED,
        "break": MarketStatus.HALTED,
        "delisted": MarketStatus.DELISTED,
        "expired": MarketStatus.EXPIRED,
        "pre_listing": MarketStatus.PRE_LISTING,
        "pre-trading": MarketStatus.PRE_LISTING,
    }.get(text, MarketStatus.UNKNOWN)


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} cannot be empty")
    return text


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


__all__ = [
    "InstrumentReferenceSnapshotProvider",
    "ReferenceSnapshot",
    "catalog_from_market_rows",
    "market_definitions_from_rows",
]
