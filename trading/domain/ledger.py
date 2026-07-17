from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from .identity import AccountKey, AssetId, InstrumentId


class LedgerBook(StrEnum):
    CASH = "cash"
    AVAILABLE = "available"
    LOCKED = "locked"
    POSITION = "position"
    MARGIN = "margin"
    BORROWED = "borrowed"
    INTEREST = "interest"
    COLLATERAL = "collateral"
    CLEARING = "clearing"
    IN_TRANSIT = "in_transit"
    TRANSFER_RECEIVABLE = "transfer_receivable"
    TRANSFER_PAYABLE = "transfer_payable"
    FEE_EXPENSE = "fee_expense"
    FUNDING_INCOME = "funding_income"
    DIVIDEND_INCOME = "dividend_income"
    REALIZED_PNL = "realized_pnl"
    EXTERNAL = "external"


class LedgerEntryType(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRADE_POSITION = "trade_position"
    TRADE_CASH = "trade_cash"
    COMMISSION = "commission"
    FUNDING = "funding"
    DIVIDEND = "dividend"
    BORROW_INTEREST = "borrow_interest"
    TRANSFER = "transfer"
    EXERCISE = "exercise"
    ASSIGNMENT = "assignment"
    SETTLEMENT = "settlement"
    CORPORATE_ACTION = "corporate_action"
    REALIZED_PNL = "realized_pnl"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    entry_id: UUID
    transaction_id: UUID
    timestamp: datetime
    account: AccountKey
    book: LedgerBook
    asset: AssetId
    amount: Decimal
    entry_type: LedgerEntryType
    reference_id: str
    instrument_id: InstrumentId | None = None
    unit_price: Decimal | None = None
    quantity_multiplier: Decimal | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("ledger timestamp must be timezone-aware")
        if self.amount == 0:
            raise ValueError("zero ledger entries are not allowed")
        if self.book is LedgerBook.POSITION and self.instrument_id is None:
            raise ValueError("position entry requires instrument id")


@dataclass(frozen=True, slots=True)
class LedgerTransaction:
    transaction_id: UUID
    timestamp: datetime
    reference_id: str
    entries: tuple[LedgerEntry, ...]

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("ledger transaction timestamp must be timezone-aware")
        if len(self.entries) < 2:
            raise ValueError("ledger transaction requires at least two entries")
        if any(entry.transaction_id != self.transaction_id for entry in self.entries):
            raise ValueError("entry transaction id mismatch")
        totals = defaultdict(Decimal)
        for entry in self.entries:
            totals[entry.asset] += entry.amount
        unbalanced = {asset: value for asset, value in totals.items() if value != 0}
        if unbalanced:
            raise ValueError(f"unbalanced ledger transaction: {unbalanced}")


class Ledger:
    def __init__(self) -> None:
        self._transactions: list[LedgerTransaction] = []
        self._ids: set[UUID] = set()
        self._entry_ids: set[UUID] = set()

    def post(self, transaction: LedgerTransaction) -> None:
        if transaction.transaction_id in self._ids:
            raise ValueError(f"duplicate ledger transaction: {transaction.transaction_id}")
        entry_ids = {entry.entry_id for entry in transaction.entries}
        if len(entry_ids) != len(transaction.entries) or entry_ids & self._entry_ids:
            raise ValueError("duplicate ledger entry")
        if self._transactions and transaction.timestamp < self._transactions[-1].timestamp:
            raise ValueError("ledger transactions must be time ordered")
        self._transactions.append(transaction)
        self._ids.add(transaction.transaction_id)
        self._entry_ids.update(entry_ids)

    @property
    def transactions(self) -> tuple[LedgerTransaction, ...]:
        return tuple(self._transactions)

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(entry for transaction in self._transactions for entry in transaction.entries)

    def book_balance(self, account: AccountKey, book: LedgerBook, asset: AssetId) -> Decimal:
        return sum((entry.amount for entry in self.entries if entry.account == account and entry.book is book and entry.asset == asset), Decimal("0"))
