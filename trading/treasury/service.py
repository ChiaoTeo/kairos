from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from trading.domain.identity import AccountKey, AssetId
from trading.domain.ledger import Ledger, LedgerBook, LedgerEntry, LedgerEntryType, LedgerTransaction
from trading.reference.identity import LocationId


class TreasuryService:
    """Posts confirmed transfer facts; it does not submit external transfers."""

    def __init__(self, ledger: Ledger, controlled_accounts: dict[LocationId, AccountKey], transit_account: AccountKey) -> None:
        self.ledger = ledger
        self.controlled_accounts = dict(controlled_accounts)
        self.transit_account = transit_account

    def post_internal_transfer(self, transfer_id: str, source: LocationId, destination: LocationId, asset: AssetId, amount: Decimal, at: datetime) -> LedgerTransaction:
        if source == destination or amount <= 0:
            raise ValueError("internal transfer requires distinct locations and positive amount")
        return self._post(transfer_id, at, (
            (self._account(source), LedgerBook.CASH, -amount),
            (self._account(destination), LedgerBook.CASH, amount),
        ), asset)

    def post_source_debit(self, transfer_id: str, source: LocationId, asset: AssetId, amount: Decimal, at: datetime) -> LedgerTransaction:
        if amount <= 0:
            raise ValueError("source debit must be positive")
        return self._post(f"{transfer_id}:source-debit", at, (
            (self._account(source), LedgerBook.CASH, -amount),
            (self.transit_account, LedgerBook.IN_TRANSIT, amount),
        ), asset)

    def post_destination_credit(self, transfer_id: str, destination: LocationId, asset: AssetId, amount: Decimal, at: datetime) -> LedgerTransaction:
        if amount <= 0:
            raise ValueError("destination credit must be positive")
        return self._post(f"{transfer_id}:destination-credit", at, (
            (self.transit_account, LedgerBook.IN_TRANSIT, -amount),
            (self._account(destination), LedgerBook.CASH, amount),
        ), asset)

    def post_transfer_fee(self, transfer_id: str, source: LocationId, asset: AssetId, amount: Decimal, at: datetime) -> LedgerTransaction:
        if amount <= 0:
            raise ValueError("transfer fee must be positive")
        return self._post(f"{transfer_id}:fee", at, (
            (self._account(source), LedgerBook.CASH, -amount),
            (self._account(source), LedgerBook.FEE_EXPENSE, amount),
        ), asset)

    def post_in_transit_fee(self, transfer_id: str, source: LocationId, asset: AssetId, amount: Decimal, at: datetime) -> LedgerTransaction:
        if amount <= 0:
            raise ValueError("transfer fee must be positive")
        return self._post(f"{transfer_id}:fee", at, (
            (self.transit_account, LedgerBook.IN_TRANSIT, -amount),
            (self._account(source), LedgerBook.FEE_EXPENSE, amount),
        ), asset)

    def post_return(self, transfer_id: str, source: LocationId, asset: AssetId, amount: Decimal, at: datetime) -> LedgerTransaction:
        if amount <= 0:
            raise ValueError("returned transfer amount must be positive")
        return self._post(f"{transfer_id}:return", at, (
            (self.transit_account, LedgerBook.IN_TRANSIT, -amount),
            (self._account(source), LedgerBook.CASH, amount),
        ), asset)

    def _account(self, location: LocationId) -> AccountKey:
        try:
            return self.controlled_accounts[location]
        except KeyError as error:
            raise LookupError(f"location is not controlled: {location}") from error

    def _post(self, reference: str, at: datetime, postings, asset: AssetId) -> LedgerTransaction:
        if at.tzinfo is None:
            raise ValueError("treasury posting time must be timezone-aware")
        transaction_id = uuid5(NAMESPACE_URL, f"treasury:{reference}")
        entries = tuple(
            LedgerEntry(uuid5(NAMESPACE_URL, f"treasury:{reference}:{index}"), transaction_id, at, account, book, asset, amount, LedgerEntryType.TRANSFER, reference)
            for index, (account, book, amount) in enumerate(postings, 1)
        )
        transaction = LedgerTransaction(transaction_id, at, reference, entries)
        self.ledger.post(transaction)
        return transaction
