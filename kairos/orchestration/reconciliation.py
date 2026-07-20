from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from kairos.application.clock import Clock, SystemClock
from kairos.ports import AccountPort, AccountState
from kairos.domain.identity import AccountKey, AssetId, InstrumentId
from kairos.domain.ledger import Ledger, LedgerBook
from kairos.risk.strategy_positions import StrategyPositionBook

if TYPE_CHECKING:
    from kairos.orchestration.runtime_store import SQLiteRuntimeStore


@dataclass(frozen=True, slots=True)
class ReconciliationDifference:
    kind: str
    key: str
    local: Decimal
    venue: Decimal


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    account: AccountKey
    checked_at: datetime
    differences: tuple[ReconciliationDifference, ...]

    @property
    def matched(self) -> bool:
        return not self.differences


class ReconciliationService:
    def __init__(self, ledger: Ledger, account_gateway: AccountPort,
                 tolerance: Decimal = Decimal("0.00000001"), clock: Clock | None = None,
                 runtime_store: "SQLiteRuntimeStore | None" = None,
                 strategy_positions: StrategyPositionBook | None = None) -> None:
        self.ledger, self.account_gateway, self.tolerance = ledger, account_gateway, tolerance
        self.clock = clock or SystemClock()
        self.runtime_store = runtime_store
        self.strategy_positions = strategy_positions

    def reconcile(self, account: AccountKey) -> ReconciliationReport:
        venue = self.account_gateway.account_state(account)
        ledger = self.runtime_store.load_ledger() if self.runtime_store is not None else self.ledger
        local_balances = defaultdict(Decimal)
        local_positions = defaultdict(Decimal)
        for entry in ledger.entries:
            if entry.account != account:
                continue
            if entry.book in {LedgerBook.CASH, LedgerBook.AVAILABLE, LedgerBook.LOCKED, LedgerBook.MARGIN, LedgerBook.COLLATERAL, LedgerBook.BORROWED}:
                local_balances[entry.asset] += entry.amount
            elif entry.book is LedgerBook.POSITION and entry.instrument_id is not None:
                local_positions[entry.instrument_id] += entry.amount
        venue_balances = defaultdict(Decimal)
        venue_positions = defaultdict(Decimal)
        for balance in venue.balances:
            venue_balances[balance.asset] += balance.total
        for instrument_id, quantity in venue.positions:
            venue_positions[instrument_id] += quantity
        differences = []
        for asset in sorted(set(local_balances) | set(venue_balances), key=lambda item: item.value):
            if abs(local_balances[asset] - venue_balances[asset]) > self.tolerance:
                differences.append(ReconciliationDifference("balance", asset.value, local_balances[asset], venue_balances[asset]))
        for instrument in sorted(set(local_positions) | set(venue_positions), key=lambda item: item.value):
            if abs(local_positions[instrument] - venue_positions[instrument]) > self.tolerance:
                differences.append(ReconciliationDifference("position", instrument.value, local_positions[instrument], venue_positions[instrument]))
        if self.runtime_store is not None:
            local_open = set(self.runtime_store.local_open_order_ids(account))
            venue_open = set(venue.open_order_ids)
            for order_id in sorted(local_open | venue_open):
                if (order_id in local_open) != (order_id in venue_open):
                    differences.append(ReconciliationDifference(
                        "open_order", order_id,
                        Decimal(int(order_id in local_open)), Decimal(int(order_id in venue_open)),
                    ))
        strategy_positions = self.strategy_positions
        if strategy_positions is None and self.runtime_store is not None:
            strategy_positions = self.runtime_store.load_strategy_position_book(account)
        if strategy_positions is not None:
            for message in strategy_positions.reconcile(dict(local_positions)):
                instrument, values = message.split(": virtual=", 1)
                virtual, account_value = values.split(" account=", 1)
                differences.append(ReconciliationDifference(
                    "strategy_position", instrument, Decimal(virtual), Decimal(account_value),
                ))
        return ReconciliationReport(account, self.clock.now(), tuple(differences))
