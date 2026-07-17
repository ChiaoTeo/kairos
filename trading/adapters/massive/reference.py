from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import re
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from trading.backtest.calendar import TradingCalendar
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.product import EquitySpec, ExerciseStyle, IndexSpec, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType
from trading.reference import (
    AssetDefinition, AssetType, ListingDefinition, ListingId, MappingTargetType,
    ProviderId, ProviderSymbolMapping, ReferenceCatalog, TradingRules,
    VenueDefinition, VenueType,
)
from trading.reference.factory import publish_instrument


class MassiveReferenceImporter:
    def __init__(self, catalog: ReferenceCatalog) -> None:
        self.catalog = catalog

    def import_underlyings(self, rows: Iterable[Mapping[str, object]], *, as_of: datetime):
        if as_of.tzinfo is None:
            raise ValueError("reference as_of must be timezone-aware")
        imported = []
        for row in sorted(rows, key=lambda item: _effective_from(item, as_of)):
            ticker = str(row["ticker"])
            market = str(row.get("market", "stocks")).lower()
            is_index = market == "indices" or ticker.startswith("I:") or str(row.get("type", "")).upper() in {"INDEX", "I"}
            namespace = "indices" if is_index else "stocks"
            internal_id = InstrumentId(f"index:us:{ticker.removeprefix('I:')}" if is_index else f"equity:us:{ticker}")
            try:
                existing_id = self._resolve(namespace, ticker, as_of)
                existing = self.catalog.instruments.get(existing_id, as_of)
                imported.append(existing)
                continue
            except LookupError:
                pass
            primary_exchange = str(row.get("primary_exchange") or "UNKNOWN")
            spec = IndexSpec(AssetId("USD"), primary_exchange) if is_index else EquitySpec(primary_exchange, "US", AssetId("USD"))
            product_type = ProductType.INDEX if is_index else ProductType.EQUITY
            effective_from = _effective_from(row, as_of)
            venue_id = VenueId(primary_exchange.lower()) if primary_exchange != "UNKNOWN" else None
            listings = () if venue_id is None else (ListingDefinition(
                ListingId(f"listing:{venue_id.value}:{internal_id.value}"), internal_id, venue_id, ticker, AssetId("USD"),
                TradingRules(Decimal("0.01"), Decimal("1"), Decimal("1")), effective_from,
            ),)
            definition = publish_instrument(
                self.catalog, instrument_id=internal_id, instrument_type=product_type, display_name=ticker,
                contract_spec=spec, trading_currency=AssetId("USD"), listings=listings, effective_from=effective_from,
                asset_definitions=(
                    AssetDefinition(AssetId("USD"), AssetType.FIAT, "US Dollar", effective_from, decimals=2),
                    *((AssetDefinition(AssetId(ticker), AssetType.SECURITY, ticker, effective_from),) if not is_index else ()),
                ),
                venue_definitions=() if venue_id is None else (
                    VenueDefinition(venue_id, VenueType.EXCHANGE, primary_exchange, "UTC", effective_from,
                                    mic=primary_exchange if len(primary_exchange) == 4 else None),
                ),
            )
            aliases = {ticker, ticker.removeprefix("I:")} if is_index else {ticker}
            for alias in aliases:
                self.catalog.add_mapping(ProviderSymbolMapping(
                    ProviderId("massive"), namespace, alias, MappingTargetType.INSTRUMENT,
                    internal_id.value, effective_from,
                ))
            imported.append(definition)
        return tuple(imported)

    def import_option_contracts(self, rows: Iterable[Mapping[str, object]], *, as_of: datetime):
        if as_of.tzinfo is None:
            raise ValueError("reference as_of must be timezone-aware")
        imported = []
        for row in rows:
            ticker = str(row["ticker"])
            underlying_ticker = str(row["underlying_ticker"])
            underlying_id = self._resolve_underlying(underlying_ticker, as_of)
            right = OptionRight.CALL if str(row["contract_type"]).lower() == "call" else OptionRight.PUT
            style = ExerciseStyle(str(row.get("exercise_style", "american")).lower())
            root = _option_root(ticker)
            cash_settled = underlying_ticker in {"I:SPX", "SPX", "I:SPXW", "SPXW"} or root in {"SPX", "SPXW"}
            session = SettlementSession.AM if root == "SPX" else SettlementSession.PM
            expiration_date = datetime.fromisoformat(str(row["expiration_date"])).date()
            eastern = ZoneInfo("America/New_York")
            expiration = datetime.combine(expiration_date, time(9, 30) if session is SettlementSession.AM else time(16), tzinfo=eastern).astimezone(timezone.utc)
            last_trade_at = expiration if session is SettlementSession.PM else _previous_session_close(expiration_date)
            internal_id = InstrumentId(f"option:us:{ticker.removeprefix('O:')}")
            try:
                existing_id = self._resolve("options", ticker, as_of)
                existing = self.catalog.instruments.get(existing_id, as_of)
                if not isinstance(existing.contract_spec, ListedOptionSpec) or existing.contract_spec.strike != Decimal(str(row["strike_price"])) or existing.contract_spec.right is not right:
                    raise ValueError(f"Massive contract conflicts with existing option definition: {ticker}")
                imported.append(existing)
                continue
            except LookupError:
                pass
            effective_from = _effective_from(row, as_of)
            spec = ListedOptionSpec(
                underlying_id, expiration, Decimal(str(row["strike_price"])), right, style,
                SettlementType.CASH if cash_settled else SettlementType.PHYSICAL, session,
                Decimal(str(row.get("shares_per_contract") or 100)), last_trade_at,
            )
            definition = publish_instrument(
                self.catalog, instrument_id=internal_id, instrument_type=ProductType.LISTED_OPTION,
                display_name=ticker, contract_spec=spec, trading_currency=AssetId("USD"), listings=(),
                effective_from=effective_from, effective_to=expiration, trading_class=root,
                asset_definitions=(
                    AssetDefinition(AssetId("USD"), AssetType.FIAT, "US Dollar", effective_from, decimals=2),
                    *((AssetDefinition(AssetId(underlying_ticker.removeprefix("I:")), AssetType.SECURITY,
                                             underlying_ticker, effective_from),) if not cash_settled else ()),
                ),
                physical_deliverable_asset=None if cash_settled else AssetId(underlying_ticker.removeprefix("I:")),
            )
            self.catalog.add_mapping(ProviderSymbolMapping(
                ProviderId("massive"), "options", ticker, MappingTargetType.INSTRUMENT,
                internal_id.value, effective_from, expiration,
            ))
            imported.append(definition)
        return tuple(imported)

    def _resolve_underlying(self, ticker: str, as_of: datetime) -> InstrumentId:
        for namespace in ("indices", "stocks"):
            try:
                return self._resolve(namespace, ticker, as_of)
            except LookupError:
                continue
        raise LookupError(f"Massive option underlying must be imported first: {ticker}")

    def _resolve(self, namespace: str, external_id: str, at: datetime) -> InstrumentId:
        mapping = self.catalog.resolve_provider_symbol(ProviderId("massive"), namespace, external_id, at)
        return InstrumentId(mapping.target_id)


def _effective_from(row: Mapping[str, object], fallback: datetime) -> datetime:
    value = row.get("listing_date") or row.get("list_date")
    if not value:
        return fallback
    return datetime.combine(datetime.fromisoformat(str(value)).date(), time.min, tzinfo=timezone.utc)


def _option_root(ticker: str) -> str:
    match = re.match(r"^O:([A-Z0-9.]+)\d{6}[CP]\d{8}$", ticker)
    if not match:
        raise ValueError(f"invalid Massive OCC option ticker: {ticker}")
    return match.group(1)


def _previous_session_close(expiration_date) -> datetime:
    calendar = TradingCalendar()
    day = expiration_date - timedelta(days=1)
    while not calendar.is_trading_day(day):
        day -= timedelta(days=1)
    return calendar.session(day).closes_at.astimezone(timezone.utc)
