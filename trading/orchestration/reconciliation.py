from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from trading.adapters.base import AccountAdapter, AccountState
from trading.domain.identity import AccountKey, AssetId, InstrumentId
from trading.domain.ledger import Ledger, LedgerBook


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
    def __init__(self, ledger: Ledger, account_adapter: AccountAdapter, tolerance: Decimal = Decimal("0.00000001")) -> None:
        self.ledger, self.account_adapter, self.tolerance = ledger, account_adapter, tolerance

    def reconcile(self, account: AccountKey) -> ReconciliationReport:
        venue = self.account_adapter.account_state(account)
        local_balances = defaultdict(Decimal)
        local_positions = defaultdict(Decimal)
        for entry in self.ledger.entries:
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
        return ReconciliationReport(account, datetime.now(timezone.utc), tuple(differences))
