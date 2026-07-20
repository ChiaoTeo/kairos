from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from kairos.domain.identity import AccountKey, AssetId, InstrumentId
from kairos.domain.ledger import Ledger, LedgerBook
from kairos.products.calculators import PositionCalculatorRegistry
from kairos.reference import ReferenceCatalog, ReferenceRole

from .conversion import AssetConversionGraph


@dataclass(frozen=True, slots=True)
class AssetBalance:
    account: AccountKey
    asset: AssetId
    total: Decimal
    available: Decimal
    locked: Decimal
    borrowed: Decimal
    interest: Decimal
    collateral: Decimal


@dataclass(frozen=True, slots=True)
class Position:
    account: AccountKey
    instrument_id: InstrumentId
    quantity: Decimal
    average_price: Decimal
    mark_price: Decimal | None
    market_value_reporting: Decimal | None
    unrealized_pnl_reporting: Decimal | None
    realized_pnl_native: Decimal
    valuation_asset: AssetId


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    timestamp: datetime
    reporting_asset: AssetId
    balances: tuple[AssetBalance, ...]
    positions: tuple[Position, ...]
    net_asset_value: Decimal
    status: str
    unpriced_assets: tuple[str, ...]
    unpriced_positions: tuple[str, ...]


class Portfolio:
    def __init__(self, ledger: Ledger, catalog: ReferenceCatalog, reporting_asset: AssetId, calculators: PositionCalculatorRegistry | None = None) -> None:
        self.ledger = ledger
        self.catalog = catalog
        self.reporting_asset = reporting_asset
        self.calculators = calculators or PositionCalculatorRegistry()

    def snapshot(
        self,
        timestamp: datetime,
        marks: dict[InstrumentId, Decimal],
        conversions: AssetConversionGraph,
        *,
        max_conversion_age: timedelta = timedelta(minutes=5),
    ) -> PortfolioSnapshot:
        balance_map = defaultdict(lambda: defaultdict(Decimal))
        for entry in self.ledger.entries:
            if entry.timestamp > timestamp:
                continue
            balance_map[(entry.account, entry.asset)][entry.book] += entry.amount
        balances = []
        nav = Decimal("0")
        unpriced_assets = []
        for (account, asset), books in sorted(balance_map.items(), key=lambda item: (item[0][0].value, item[0][1].value)):
            if asset.value.startswith("POSITION:"):
                continue
            total = books[LedgerBook.CASH] + books[LedgerBook.AVAILABLE] + books[LedgerBook.LOCKED] + books[LedgerBook.MARGIN] + books[LedgerBook.COLLATERAL] + books[LedgerBook.BORROWED]
            balance = AssetBalance(account, asset, total, books[LedgerBook.AVAILABLE], books[LedgerBook.LOCKED], -books[LedgerBook.BORROWED], books[LedgerBook.INTEREST], books[LedgerBook.COLLATERAL])
            balances.append(balance)
            if total:
                try:
                    nav += conversions.convert(total, asset, self.reporting_asset, timestamp, max_conversion_age).amount
                except LookupError:
                    unpriced_assets.append(f"{account.value}:{asset.value}")
        positions = []
        unpriced_positions = []
        grouped = defaultdict(list)
        for entry in self.ledger.entries:
            if entry.timestamp <= timestamp and entry.book is LedgerBook.POSITION:
                grouped[(entry.account, entry.instrument_id)].append(entry)
        for (account, instrument_id), entries in sorted(grouped.items(), key=lambda item: (item[0][0].value, item[0][1].value)):
            definition = _definition(self.catalog, instrument_id, timestamp)
            quantity, average, realized = _position_cost(entries, definition, self.calculators)
            if quantity == 0:
                continue
            mark = marks.get(instrument_id)
            calculator = self.calculators.for_definition(definition)
            valuation_asset = _valuation_asset(definition)
            market_value_reporting = unrealized_reporting = None
            if mark is None:
                unpriced_positions.append(instrument_id.value)
            else:
                native_market_value = calculator.market_value(definition, quantity, mark, average)
                native_unrealized = calculator.unrealized_pnl(definition, quantity, mark, average)
                try:
                    market_value_reporting = conversions.convert(native_market_value, valuation_asset, self.reporting_asset, timestamp, max_conversion_age).amount
                    unrealized_reporting = conversions.convert(native_unrealized, valuation_asset, self.reporting_asset, timestamp, max_conversion_age).amount
                    nav += market_value_reporting
                except LookupError:
                    unpriced_positions.append(instrument_id.value)
            positions.append(Position(account, instrument_id, quantity, average, mark, market_value_reporting, unrealized_reporting, realized, valuation_asset))
        status = "complete" if not unpriced_assets and not unpriced_positions else "partial"
        return PortfolioSnapshot(timestamp, self.reporting_asset, tuple(balances), tuple(positions), nav, status, tuple(unpriced_assets), tuple(unpriced_positions))


def _position_cost(entries, definition, calculators):
    quantity = Decimal("0")
    average = Decimal("0")
    realized = Decimal("0")
    calculator = calculators.for_definition(definition)
    for entry in entries:
        if entry.entry_type.value == "corporate_action" and entry.quantity_multiplier is not None:
            quantity *= entry.quantity_multiplier
            average /= entry.quantity_multiplier
            continue
        trade_quantity = entry.amount
        price = entry.unit_price
        if price is None:
            if entry.entry_type.value == "corporate_action":
                quantity += trade_quantity
                if quantity == 0:
                    average = Decimal("0")
            continue
        if quantity == 0 or quantity * trade_quantity > 0:
            total = abs(quantity) + abs(trade_quantity)
            average = (average * abs(quantity) + price * abs(trade_quantity)) / total
            quantity += trade_quantity
        else:
            closing = min(abs(quantity), abs(trade_quantity))
            realized += calculator.realized_pnl(definition, closing, price, average, 1 if quantity > 0 else -1)
            new_quantity = quantity + trade_quantity
            if new_quantity == 0:
                quantity, average = Decimal("0"), Decimal("0")
            elif quantity * new_quantity < 0:
                quantity, average = new_quantity, price
            else:
                quantity = new_quantity
    return quantity, average, realized


def _valuation_asset(definition):
    spec = definition.contract_spec
    if hasattr(spec, "settlement_asset"):
        return spec.settlement_asset
    if hasattr(spec, "quote_asset"):
        return spec.quote_asset
    if hasattr(spec, "trading_currency"):
        return spec.trading_currency
    raise ValueError(f"definition has no valuation asset: {definition.instrument_id}")


def _definition(catalog, instrument_id, at):
    return catalog.instruments.get(instrument_id, at)
