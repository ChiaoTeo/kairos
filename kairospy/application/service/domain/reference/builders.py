from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from kairospy.core.reference import (
    Asset,
    AssetType,
    Entity,
    EntityId,
    EntityType,
    InstrumentDefinition,
    InstrumentType,
    ListingDefinition,
    MarketDefinition,
    MarketStatus,
    ReferenceCatalog,
)
from kairospy.core.reference.identity import AssetId, InstrumentId, ListingId, MarketId, reference_slug


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
        market = _required_text(row.get("market"), "market")
        base = _optional_text(row.get("base"))
        quote = _optional_text(row.get("quote"))
        base_asset_type = _asset_type_for_market(market, quote=False)
        quote_asset_type = _asset_type_for_market(market, quote=True)
        base_asset_id = _asset_id(base_asset_type, base) if base else None
        quote_asset_id = _asset_id(quote_asset_type, quote) if quote else None
        for symbol, asset_id, asset_type in (
            (base, base_asset_id, base_asset_type),
            (quote, quote_asset_id, quote_asset_type),
        ):
            if symbol and asset_id and str(asset_id) not in seen_assets:
                catalog.add_asset(Asset(asset_id, asset_type, symbol, effective_from=effective_from))
                seen_assets.add(str(asset_id))

        venue = _required_text(row.get("venue"), "venue")
        source_symbol = _required_text(row.get("source_symbol"), "source_symbol")
        instrument_id = _instrument_id_for_market(
            market,
            base=base,
            quote=quote,
            venue=venue,
            source_symbol=source_symbol,
        )
        if str(instrument_id) not in seen_instruments:
            catalog.add_instrument(
                InstrumentDefinition(
                    instrument_id,
                    _instrument_type_for_market(market),
                    base_asset_id=base_asset_id,
                    quote_asset_id=quote_asset_id,
                    display_name=_display_name(base, quote, row),
                    effective_from=effective_from,
                )
            )
            seen_instruments.add(str(instrument_id))

        listing_id = _listing_id(venue=venue, market=market, source_symbol=source_symbol)
        status = _market_status(row)
        if str(listing_id) not in seen_listings:
            catalog.add_listing(
                ListingDefinition(
                    listing_id,
                    instrument_id,
                    venue,
                    source_symbol,
                    venue_instrument_id=_optional_text(row.get("venue_instrument_id")),
                    currency_asset_id=quote_asset_id,
                    status=status,
                    effective_from=effective_from,
                )
            )
            seen_listings.add(str(listing_id))

        market_id = _market_id(venue=venue, market=market, source_symbol=source_symbol)
        if str(market_id) not in seen_markets:
            catalog.add_market(
                MarketDefinition(
                    market_id,
                    instrument_id,
                    listing_id,
                    venue,
                    market,
                    source_symbol,
                    status=status,
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


def catalog_from_reference_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    effective_from: datetime,
    product: object | None = None,
) -> ReferenceCatalog:
    product_name = _optional_text(product)
    if product_name is not None and product_name.strip().lower() in {"equity", "stock", "stocks"}:
        return catalog_from_equity_rows(rows, effective_from=effective_from)
    return catalog_from_market_rows(rows, effective_from=effective_from)


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
        asset_id = _equity_asset_id(stable_key)
        instrument_id = _equity_instrument_id(stable_key)
        listing_id = _equity_listing_id(venue=venue, stable_key=stable_key)
        market_id = _equity_market_id(venue=venue, stable_key=stable_key)
        currency = _optional_text(row.get("currency")) or "USD"
        quote_asset_id = _currency_asset_id(currency)

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
        if str(quote_asset_id) not in seen_assets:
            catalog.add_asset(Asset(quote_asset_id, AssetType.FIAT, currency, effective_from=effective_from))
            seen_assets.add(str(quote_asset_id))
        if str(instrument_id) not in seen_instruments:
            catalog.add_instrument(
                InstrumentDefinition(
                    instrument_id,
                    InstrumentType.EQUITY,
                    base_asset_id=asset_id,
                    quote_asset_id=quote_asset_id,
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
                    currency_asset_id=quote_asset_id,
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


def market_definitions_from_rows(rows: Iterable[Mapping[str, object]], *, effective_from: datetime) -> tuple[MarketDefinition, ...]:
    return catalog_from_market_rows(rows, effective_from=effective_from).list_markets(at=effective_from)


def _stable_key(row: Mapping[str, object], ticker: str) -> str:
    for key in ("instrument_id", "asset_id", "entity_id", "cik", "figi", "composite_figi", "share_class_figi"):
        value = _optional_text(row.get(key))
        if value:
            return reference_slug(value.removeprefix("instrument:").removeprefix("asset:").removeprefix("entity:"))
    return reference_slug(ticker)


def _display_name(base: str | None, quote: str | None, row: Mapping[str, object]) -> str:
    if base and quote:
        return f"{base}/{quote}"
    return _required_text(row.get("source_symbol"), "source_symbol")


def _instrument_type_for_market(market: object) -> InstrumentType:
    text = _normalized_market(market)
    if text == "spot":
        return InstrumentType.SPOT
    if text in {"swap", "perp", "perpetual", "derivative"}:
        return InstrumentType.PERPETUAL
    if text in {"future", "futures"}:
        return InstrumentType.FUTURE
    if text in {"option", "options"}:
        return InstrumentType.OPTION
    if text == "equity":
        return InstrumentType.EQUITY
    return InstrumentType.OTHER


def _asset_type_for_market(market: object, *, quote: bool) -> AssetType:
    text = _normalized_market(market)
    if text in {"spot", "swap", "perp", "perpetual", "future", "futures", "derivative"}:
        return AssetType.CRYPTO
    if text in {"option", "options"}:
        return AssetType.FIAT if quote else AssetType.OTHER
    if text == "equity":
        return AssetType.FIAT if quote else AssetType.EQUITY
    return AssetType.OTHER


def _instrument_id_for_market(
    market_type: object,
    *,
    base: str | None,
    quote: str | None,
    venue: object,
    source_symbol: object,
) -> InstrumentId:
    segment = _instrument_identity_segment(market_type)
    if base and quote:
        return InstrumentId(f"instrument:{segment}:{reference_slug(base)}:{reference_slug(quote)}")
    return InstrumentId(f"instrument:{segment}:{reference_slug(venue)}:{reference_slug(market_type)}:{reference_slug(source_symbol)}")


def _instrument_identity_segment(market: object) -> str:
    text = _normalized_market(market)
    if text in {"swap", "perp"}:
        return "perpetual"
    if text == "futures":
        return "future"
    if text == "options":
        return "option"
    return reference_slug(text or "other")


def _asset_id(asset_type: AssetType, symbol: str) -> AssetId:
    return AssetId(f"asset:{asset_type.value}:{reference_slug(symbol)}")


def _equity_asset_id(stable_key: str) -> AssetId:
    return AssetId(f"asset:{AssetType.EQUITY.value}:{reference_slug(stable_key)}")


def _currency_asset_id(currency: str) -> AssetId:
    return AssetId(f"asset:{AssetType.FIAT.value}:{reference_slug(currency)}")


def _equity_instrument_id(stable_key: str) -> InstrumentId:
    return InstrumentId(f"instrument:equity:{reference_slug(stable_key)}")


def _listing_id(*, venue: object, market: object, source_symbol: object) -> ListingId:
    return ListingId(f"listing:{reference_slug(venue)}:{reference_slug(market)}:{reference_slug(source_symbol)}")


def _equity_listing_id(*, venue: object, stable_key: str) -> ListingId:
    return ListingId(f"listing:{reference_slug(venue)}:{reference_slug(stable_key)}")


def _market_id(*, venue: object, market: object, source_symbol: object) -> MarketId:
    return MarketId(f"market:{reference_slug(venue)}:{reference_slug(market)}:{reference_slug(source_symbol)}")


def _equity_market_id(*, venue: object, stable_key: str) -> MarketId:
    return MarketId(f"market:{reference_slug(venue)}:equity:{reference_slug(stable_key)}")


def _normalized_market(value: object) -> str:
    return str(value or "").strip().lower()


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
        "break": MarketStatus.HALTED,
        "delisted": MarketStatus.DELISTED,
        "expired": MarketStatus.EXPIRED,
        "pre_listing": MarketStatus.PRE_LISTING,
        "pre-trading": MarketStatus.PRE_LISTING,
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
    "ReferenceSnapshot",
    "catalog_from_equity_rows",
    "catalog_from_market_rows",
    "catalog_from_reference_rows",
    "market_definitions_from_rows",
]
