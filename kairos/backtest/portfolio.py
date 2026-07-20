from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid5, NAMESPACE_URL

from kairos.accounting.ledger import LedgerService
from kairos.domain.execution import TradeExecution, TradeSide
from kairos.domain.identity import AccountKey, AssetId, InstrumentId
from kairos.domain.ledger import Ledger, LedgerBook
from kairos.domain.order import Fill, Settlement
from kairos.domain.product import is_option_spec, option_multiplier
from kairos.reference import ReferenceCatalog
from kairos.reference.access import contract_spec, definition_at, trade_cash_asset
from kairos.risk.option_structure import maximum_expiry_loss

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Position:
    instrument_id: InstrumentId
    quantity: Decimal
    average_price: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class StructurePosition:
    structure_id: UUID
    strategy_id: str
    quantity: int
    entry_net_price: Decimal
    opened_at: datetime
    legs: tuple[tuple[InstrumentId, int], ...]


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    instrument_id: InstrumentId
    quantity: Decimal
    average_price: Decimal
    mark_mid: Decimal | None
    mark_liquidation: Decimal | None
    market_value_mid: Decimal | None
    market_value_liquidation: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl_mid: Decimal | None
    mark_source: str


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    timestamp: datetime
    initial_cash: Decimal
    cash: Decimal
    equity_mid: Decimal
    equity_liquidation: Decimal
    peak_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl_mid: Decimal
    commissions: Decimal
    slippage: Decimal
    positions: tuple[PositionSnapshot, ...]
    open_structures: tuple[StructurePosition, ...]
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    greeks_coverage: Decimal
    missing_greeks: tuple[str, ...]
    unpriced_positions: tuple[str, ...]
    max_theoretical_risk: Decimal
    fallback_price_count: int


class BacktestPortfolio:
    """Ledger-backed portfolio used by research backtests and simulation."""

    def __init__(self, initial_cash: Decimal, catalog: ReferenceCatalog, account: AccountKey, cash_asset: AssetId = AssetId("USD")) -> None:
        if initial_cash <= 0:
            raise ValueError("initial cash must be positive")
        self.initial_cash = initial_cash
        self.catalog = catalog
        self.account = account
        self.cash_asset = cash_asset
        self.ledger = Ledger()
        self.ledger_service = LedgerService(self.ledger, catalog)
        self.ledger_service.deposit(account, cash_asset, initial_cash, datetime.min.replace(tzinfo=timezone.utc), "backtest-initial")
        self.structures: dict[UUID, StructurePosition] = {}
        self.applied_fills: set[UUID] = set()
        self.applied_settlements: set[UUID] = set()
        self.slippage = ZERO
        self.peak_equity = initial_cash

    @property
    def cash(self) -> Decimal:
        return self.ledger.book_balance(self.account, LedgerBook.CASH, self.cash_asset)

    @property
    def commissions(self) -> Decimal:
        return self.ledger.book_balance(self.account, LedgerBook.FEE_EXPENSE, self.cash_asset)

    @property
    def positions(self) -> dict[InstrumentId, Position]:
        return {instrument_id: self._position(instrument_id) for instrument_id in self._position_ids()}

    def apply_fill(self, fill: Fill) -> None:
        if fill.fill_id in self.applied_fills:
            raise ValueError(f"duplicate fill: {fill.fill_id}")
        for index, leg in enumerate(fill.legs):
            definition = definition_at(self.catalog, leg.instrument_id, fill.timestamp)
            self.ledger_service.trade(TradeExecution(
                uuid5(NAMESPACE_URL, f"fill:{fill.fill_id}:{index}"), fill.timestamp, self.account,
                leg.instrument_id, leg.side, Decimal(leg.ratio * fill.quantity), leg.price,
                trade_cash_asset(self.catalog, definition, fill.timestamp), fill.commission if index == 0 else ZERO, str(fill.order_id),
            ))
        self.applied_fills.add(fill.fill_id)
        self.slippage += fill.slippage
        signed_legs = tuple((leg.instrument_id, leg.side.sign * leg.ratio) for leg in fill.legs)
        if fill.is_closing:
            structure = self.structures.get(fill.structure_id)
            if structure is None or fill.quantity > structure.quantity:
                raise ValueError("closing fill exceeds open structure")
            remaining = structure.quantity - fill.quantity
            if remaining:
                self.structures[fill.structure_id] = StructurePosition(
                    structure.structure_id, structure.strategy_id, remaining, structure.entry_net_price,
                    structure.opened_at, structure.legs,
                )
            else:
                del self.structures[fill.structure_id]
        elif fill.structure_id in self.structures:
            structure = self.structures[fill.structure_id]
            self.structures[fill.structure_id] = StructurePosition(
                structure.structure_id, structure.strategy_id, structure.quantity + fill.quantity,
                structure.entry_net_price, structure.opened_at, structure.legs,
            )
        else:
            self.structures[fill.structure_id] = StructurePosition(
                fill.structure_id, fill.strategy_id, fill.quantity, fill.net_price, fill.timestamp, signed_legs,
            )

    def apply_settlement(self, settlement: Settlement) -> None:
        if settlement.settlement_id in self.applied_settlements:
            raise ValueError(f"duplicate settlement: {settlement.settlement_id}")
        position = self._position(settlement.instrument_id)
        if position.quantity != settlement.position_quantity:
            raise ValueError("settlement quantity does not match portfolio")
        self.ledger_service.settle_position(
            self.account, settlement.instrument_id, position.quantity, settlement.intrinsic_value,
            settlement.timestamp, str(settlement.settlement_id),
        )
        self.applied_settlements.add(settlement.settlement_id)
        self.structures.pop(settlement.structure_id, None)

    def snapshot(self, market) -> PortfolioSnapshot:
        by_instrument = {item.instrument_id: item for item in market.instruments}
        values_mid = values_liquidation = realized = unrealized = ZERO
        snapshots, unpriced, missing_greeks = [], [], []
        greeks_totals = {name: ZERO for name in ("delta", "gamma", "theta", "vega")}
        greek_covered = nonzero_count = fallback_count = 0
        for instrument_id, position in sorted(self.positions.items(), key=lambda item: item[0].value):
            realized += position.realized_pnl
            if position.quantity == 0:
                continue
            nonzero_count += 1
            item = by_instrument.get(instrument_id)
            quote = item.quote if item else None
            mid = (quote.bid + quote.ask) / 2 if quote and quote.bid is not None and quote.ask is not None else None
            liquidation = quote.bid if quote and position.quantity > 0 else quote.ask if quote else None
            mark_source = "mid"
            if mid is None and liquidation is not None:
                mid = liquidation
                mark_source = "fallback_bid" if position.quantity > 0 else "fallback_ask"
                fallback_count += 1
            elif mid is None:
                mark_source = "unpriced"
            definition = definition_at(self.catalog, instrument_id, market.timestamp)
            multiplier = _multiplier(contract_spec(definition))
            mv_mid = position.quantity * mid * multiplier if mid is not None else None
            mv_liq = position.quantity * liquidation * multiplier if liquidation is not None else None
            cost_value = position.quantity * position.average_price * multiplier
            upnl = mv_mid - cost_value if mv_mid is not None else None
            if mv_mid is None or mv_liq is None:
                unpriced.append(instrument_id.value)
            else:
                values_mid += mv_mid
                values_liquidation += mv_liq
                unrealized += upnl or ZERO
            if item and item.greeks and all(getattr(item.greeks, name) is not None for name in greeks_totals):
                greek_covered += 1
                for name in greeks_totals:
                    greeks_totals[name] += position.quantity * multiplier * getattr(item.greeks, name)
            else:
                missing_greeks.append(instrument_id.value)
            snapshots.append(PositionSnapshot(
                instrument_id, position.quantity, position.average_price, mid, liquidation,
                mv_mid, mv_liq, position.realized_pnl, upnl, mark_source,
            ))
        equity_mid, equity_liquidation = self.cash + values_mid, self.cash + values_liquidation
        self.peak_equity = max(self.peak_equity, equity_liquidation)
        coverage = Decimal(greek_covered) / Decimal(nonzero_count) if nonzero_count else Decimal("1")
        risks = [self._structure_max_risk(structure, market.timestamp) for structure in self.structures.values()]
        greek_values = tuple(greeks_totals[name] if not missing_greeks else None for name in ("delta", "gamma", "theta", "vega"))
        return PortfolioSnapshot(
            market.timestamp, self.initial_cash, self.cash, equity_mid, equity_liquidation, self.peak_equity,
            realized, unrealized, self.commissions, self.slippage, tuple(snapshots), tuple(self.structures.values()),
            *greek_values, coverage, tuple(missing_greeks), tuple(unpriced), sum(risks, ZERO), fallback_count,
        )

    def _position_ids(self) -> set[InstrumentId]:
        return {
            entry.instrument_id for entry in self.ledger.entries
            if entry.account == self.account and entry.book is LedgerBook.POSITION and entry.instrument_id is not None
        }

    def _position(self, instrument_id: InstrumentId) -> Position:
        quantity = average = realized = ZERO
        definition = None
        for entry in self.ledger.entries:
            if entry.account != self.account or entry.book is not LedgerBook.POSITION or entry.instrument_id != instrument_id or entry.unit_price is None:
                continue
            definition = definition or definition_at(self.catalog, instrument_id, entry.timestamp)
            trade_quantity = entry.amount
            if quantity == 0 or quantity * trade_quantity > 0:
                total = abs(quantity) + abs(trade_quantity)
                average = (average * abs(quantity) + entry.unit_price * abs(trade_quantity)) / total
                quantity += trade_quantity
            else:
                closing = min(abs(quantity), abs(trade_quantity))
                realized += closing * (entry.unit_price - average) * (Decimal("1") if quantity > 0 else Decimal("-1")) * _multiplier(contract_spec(definition))
                new_quantity = quantity + trade_quantity
                if new_quantity == 0:
                    quantity = average = ZERO
                elif quantity * new_quantity < 0:
                    quantity, average = new_quantity, entry.unit_price
                else:
                    quantity = new_quantity
        return Position(instrument_id, quantity, average, realized)

    def _structure_max_risk(self, structure: StructurePosition, at: datetime) -> Decimal:
        options = []
        for instrument_id, sign in structure.legs:
            definition = definition_at(self.catalog, instrument_id, at)
            if is_option_spec(contract_spec(definition)):
                options.append((contract_spec(definition),sign))
        if options:
            return maximum_expiry_loss(tuple(options),structure.entry_net_price,structure.quantity)
        return Decimal("Infinity")


def _multiplier(spec) -> Decimal:
    return option_multiplier(spec) if is_option_spec(spec) else getattr(spec,"contract_size",Decimal("1"))
