from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import re
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from trading.backtest.calendar import TradingCalendar
from trading.catalog.external import ExternalInstrumentMapping, ExternalMappingRepository
from trading.catalog.service import InstrumentCatalog
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, VenueListing
from trading.domain.product import EquitySpec, ExerciseStyle, IndexSpec, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType


MASSIVE_VENUE = VenueId("massive")


class MassiveReferenceImporter:
    def __init__(self, catalog: InstrumentCatalog, mappings: ExternalMappingRepository) -> None:
        self.catalog, self.mappings = catalog, mappings

    def import_underlyings(self, rows: Iterable[Mapping[str, object]], *, as_of: datetime) -> tuple[InstrumentDefinition, ...]:
        if as_of.tzinfo is None:
            raise ValueError("reference as_of must be timezone-aware")
        imported = []
        for row in rows:
            ticker = str(row["ticker"])
            market = str(row.get("market", "stocks")).lower()
            is_index = market == "indices" or ticker.startswith("I:") or str(row.get("type", "")).upper() in {"INDEX", "I"}
            namespace = "indices" if is_index else "stocks"
            internal_id = InstrumentId(f"index:us:{ticker.removeprefix('I:')}" if is_index else f"equity:us:{ticker}")
            try:
                existing_id = self.mappings.resolve("massive", namespace, ticker, as_of)
                existing = self.catalog.get(existing_id, as_of)
                imported.append(existing)
                continue
            except LookupError:
                pass
            primary_exchange = str(row.get("primary_exchange") or "UNKNOWN")
            spec = IndexSpec(AssetId("USD"), primary_exchange) if is_index else EquitySpec(primary_exchange, "US", AssetId("USD"))
            product_type = ProductType.INDEX if is_index else ProductType.EQUITY
            effective_from = _effective_from(row, as_of)
            definition = InstrumentDefinition(
                internal_id, product_type, ticker, None, AssetId("USD"), spec,
                (VenueListing(MASSIVE_VENUE, ticker, ticker, Decimal("0.01"), Decimal("1"), Decimal("1"), listed_at=effective_from),),
                effective_from,
            )
            try:
                self.catalog.add(definition)
            except ValueError:
                existing = self.catalog.get(internal_id, as_of)
                if existing != definition:
                    raise
                definition = existing
            aliases = {ticker, ticker.removeprefix("I:")} if is_index else {ticker}
            for alias in aliases:
                self.mappings.add(ExternalInstrumentMapping("massive", namespace, alias, internal_id, effective_from))
            imported.append(definition)
        return tuple(imported)

    def import_option_contracts(self, rows: Iterable[Mapping[str, object]], *, as_of: datetime) -> tuple[InstrumentDefinition, ...]:
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
                existing_id = self.mappings.resolve("massive", "options", ticker, as_of)
                existing = self.catalog.get(existing_id, as_of)
                if not isinstance(existing.product_spec, ListedOptionSpec) or existing.product_spec.strike != Decimal(str(row["strike_price"])) or existing.product_spec.right is not right:
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
            definition = InstrumentDefinition(
                internal_id, ProductType.LISTED_OPTION, ticker, None, AssetId("USD"), spec,
                (VenueListing(MASSIVE_VENUE, ticker, ticker, Decimal("0.01"), Decimal("1"), Decimal("1"), listed_at=effective_from, delisted_at=expiration),),
                effective_from, expiration,
            )
            try:
                self.catalog.add(definition)
            except ValueError:
                existing = self.catalog.get(internal_id, as_of)
                if existing != definition:
                    raise
                definition = existing
            self.mappings.add(ExternalInstrumentMapping("massive", "options", ticker, internal_id, effective_from, expiration))
            imported.append(definition)
        return tuple(imported)

    def _resolve_underlying(self, ticker: str, as_of: datetime) -> InstrumentId:
        for namespace in ("indices", "stocks"):
            try:
                return self.mappings.resolve("massive", namespace, ticker, as_of)
            except LookupError:
                continue
        raise LookupError(f"Massive option underlying must be imported first: {ticker}")


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
