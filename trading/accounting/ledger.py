from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from trading.catalog.service import InstrumentCatalog
from trading.domain.execution import DividendPayment, FundingPayment, TradeExecution
from trading.domain.identity import AccountKey, AssetId
from trading.domain.ledger import Ledger, LedgerBook, LedgerEntry, LedgerEntryType, LedgerTransaction
from trading.domain.product import (
    CryptoOptionSpec, CryptoSpotSpec, EquitySpec, ListedOptionSpec, ProductType,
)
from trading.products.calculators import PositionCalculatorRegistry


class LedgerService:
    def __init__(self, ledger: Ledger, catalog: InstrumentCatalog) -> None:
        self.ledger = ledger
        self.catalog = catalog
        self.calculators = PositionCalculatorRegistry()

    def deposit(self, account: AccountKey, asset: AssetId, amount: Decimal, timestamp: datetime, reference_id: str) -> LedgerTransaction:
        if amount <= 0:
            raise ValueError("deposit amount must be positive")
        transaction = self._transaction(
            f"deposit:{reference_id}", timestamp, reference_id,
            ((account, LedgerBook.CASH, asset, amount, LedgerEntryType.DEPOSIT, None, None, None),
             (account, LedgerBook.EXTERNAL, asset, -amount, LedgerEntryType.DEPOSIT, None, None, None)),
        )
        self.ledger.post(transaction)
        return transaction

    def withdrawal(self, account: AccountKey, asset: AssetId, amount: Decimal, timestamp: datetime, reference_id: str) -> LedgerTransaction:
        if amount <= 0:
            raise ValueError("withdrawal amount must be positive")
        transaction = self._transaction(
            f"withdrawal:{reference_id}", timestamp, reference_id,
            ((account, LedgerBook.CASH, asset, -amount, LedgerEntryType.WITHDRAWAL, None, None, None),
             (account, LedgerBook.EXTERNAL, asset, amount, LedgerEntryType.WITHDRAWAL, None, None, None)),
        )
        self.ledger.post(transaction)
        return transaction

    def transfer(self, source: AccountKey, destination: AccountKey, asset: AssetId, amount: Decimal, timestamp: datetime, reference_id: str) -> LedgerTransaction:
        if source == destination or amount <= 0:
            raise ValueError("transfer requires distinct accounts and a positive amount")
        transaction = self._transaction(
            f"transfer:{reference_id}", timestamp, reference_id,
            ((source, LedgerBook.CASH, asset, -amount, LedgerEntryType.TRANSFER, None, None, None),
             (destination, LedgerBook.CASH, asset, amount, LedgerEntryType.TRANSFER, None, None, None)),
        )
        self.ledger.post(transaction)
        return transaction

    def borrow_interest(self, account: AccountKey, asset: AssetId, amount: Decimal, timestamp: datetime, reference_id: str, instrument_id=None) -> LedgerTransaction:
        if amount <= 0:
            raise ValueError("borrow interest must be positive")
        transaction = self._transaction(
            f"borrow-interest:{reference_id}", timestamp, reference_id,
            ((account, LedgerBook.CASH, asset, -amount, LedgerEntryType.BORROW_INTEREST, instrument_id, None, None),
             (account, LedgerBook.INTEREST, asset, amount, LedgerEntryType.BORROW_INTEREST, instrument_id, None, None)),
        )
        self.ledger.post(transaction)
        return transaction

    def borrow_asset(self, account: AccountKey, asset: AssetId, amount: Decimal, timestamp: datetime, reference_id: str) -> LedgerTransaction:
        if amount <= 0:
            raise ValueError("borrow amount must be positive")
        transaction = self._transaction(
            f"borrow:{reference_id}", timestamp, reference_id,
            ((account, LedgerBook.AVAILABLE, asset, amount, LedgerEntryType.TRANSFER, None, None, None),
             (account, LedgerBook.BORROWED, asset, -amount, LedgerEntryType.TRANSFER, None, None, None)),
        )
        self.ledger.post(transaction)
        return transaction

    def repay_asset(self, account: AccountKey, asset: AssetId, amount: Decimal, timestamp: datetime, reference_id: str) -> LedgerTransaction:
        if amount <= 0:
            raise ValueError("repayment amount must be positive")
        transaction = self._transaction(
            f"repay:{reference_id}", timestamp, reference_id,
            ((account, LedgerBook.AVAILABLE, asset, -amount, LedgerEntryType.TRANSFER, None, None, None),
             (account, LedgerBook.BORROWED, asset, amount, LedgerEntryType.TRANSFER, None, None, None)),
        )
        self.ledger.post(transaction)
        return transaction

    def reclassify_balance(self, account: AccountKey, asset: AssetId, amount: Decimal, source_book: LedgerBook, destination_book: LedgerBook, timestamp: datetime, reference_id: str) -> LedgerTransaction:
        owned = {LedgerBook.CASH, LedgerBook.AVAILABLE, LedgerBook.LOCKED, LedgerBook.MARGIN, LedgerBook.COLLATERAL}
        if amount <= 0 or source_book not in owned or destination_book not in owned or source_book is destination_book:
            raise ValueError("invalid balance reclassification")
        transaction = self._transaction(
            f"balance-reclassification:{reference_id}", timestamp, reference_id,
            ((account, source_book, asset, -amount, LedgerEntryType.TRANSFER, None, None, None),
             (account, destination_book, asset, amount, LedgerEntryType.TRANSFER, None, None, None)),
        )
        self.ledger.post(transaction)
        return transaction

    def trade(self, execution: TradeExecution) -> LedgerTransaction:
        definition = self.catalog.get(execution.instrument_id, execution.timestamp)
        signed_quantity = execution.quantity * execution.side.sign
        position_asset = AssetId(f"POSITION:{execution.instrument_id.value}")
        entries = [
            (execution.account, LedgerBook.POSITION, position_asset, signed_quantity, LedgerEntryType.TRADE_POSITION, execution.instrument_id, execution.price, None),
            (execution.account, LedgerBook.CLEARING, position_asset, -signed_quantity, LedgerEntryType.TRADE_POSITION, execution.instrument_id, execution.price, None),
        ]
        if definition.product_type in {ProductType.EQUITY, ProductType.ETF, ProductType.CRYPTO_SPOT, ProductType.LISTED_OPTION, ProductType.CRYPTO_OPTION, ProductType.TOKENIZED_EQUITY}:
            multiplier = _premium_multiplier(definition.product_spec)
            cash_amount = -signed_quantity * execution.price * multiplier
            entries.extend((
                (execution.account, LedgerBook.CASH, definition.quote_asset, cash_amount, LedgerEntryType.TRADE_CASH, execution.instrument_id, execution.price, None),
                (execution.account, LedgerBook.CLEARING, definition.quote_asset, -cash_amount, LedgerEntryType.TRADE_CASH, execution.instrument_id, execution.price, None),
            ))
        elif definition.product_type in {ProductType.FUTURE, ProductType.PERPETUAL}:
            quantity, average = self._position_state(execution.account, execution.instrument_id)
            if quantity and quantity * signed_quantity < 0:
                closing = min(abs(quantity), abs(signed_quantity))
                calculator = self.calculators.for_definition(definition)
                realized = calculator.realized_pnl(definition, closing, execution.price, average, 1 if quantity > 0 else -1)
                settlement_asset = definition.product_spec.settlement_asset
                if realized:
                    entries.extend((
                        (execution.account, LedgerBook.CASH, settlement_asset, realized, LedgerEntryType.REALIZED_PNL, execution.instrument_id, execution.price, None),
                        (execution.account, LedgerBook.REALIZED_PNL, settlement_asset, -realized, LedgerEntryType.REALIZED_PNL, execution.instrument_id, execution.price, None),
                    ))
        if execution.fee:
            entries.extend((
                (execution.account, LedgerBook.CASH, execution.fee_asset, -execution.fee, LedgerEntryType.COMMISSION, execution.instrument_id, None, None),
                (execution.account, LedgerBook.FEE_EXPENSE, execution.fee_asset, execution.fee, LedgerEntryType.COMMISSION, execution.instrument_id, None, None),
            ))
        transaction = self._transaction(
            f"execution:{execution.execution_id}", execution.timestamp, execution.order_id, tuple(entries)
        )
        self.ledger.post(transaction)
        return transaction

    def _position_state(self, account: AccountKey, instrument_id):
        quantity = Decimal("0")
        average = Decimal("0")
        for entry in self.ledger.entries:
            if entry.account != account or entry.book is not LedgerBook.POSITION or entry.instrument_id != instrument_id or entry.unit_price is None:
                continue
            trade_quantity = entry.amount
            if quantity == 0 or quantity * trade_quantity > 0:
                total = abs(quantity) + abs(trade_quantity)
                average = (average * abs(quantity) + entry.unit_price * abs(trade_quantity)) / total
                quantity += trade_quantity
            else:
                new_quantity = quantity + trade_quantity
                if new_quantity == 0:
                    quantity, average = Decimal("0"), Decimal("0")
                elif quantity * new_quantity < 0:
                    quantity, average = new_quantity, entry.unit_price
                else:
                    quantity = new_quantity
        return quantity, average

    def funding(self, payment: FundingPayment) -> LedgerTransaction:
        if payment.amount == 0:
            raise ValueError("zero funding payment is not allowed")
        transaction = self._transaction(
            f"funding:{payment.payment_id}", payment.timestamp, str(payment.payment_id),
            ((payment.account, LedgerBook.CASH, payment.settlement_asset, payment.amount, LedgerEntryType.FUNDING, payment.instrument_id, None, None),
             (payment.account, LedgerBook.FUNDING_INCOME, payment.settlement_asset, -payment.amount, LedgerEntryType.FUNDING, payment.instrument_id, None, None)),
        )
        self.ledger.post(transaction)
        return transaction

    def dividend(self, payment: DividendPayment) -> LedgerTransaction:
        net = payment.gross_amount - payment.withholding_tax
        if net <= 0:
            raise ValueError("dividend net amount must be positive")
        items = [
            (payment.account, LedgerBook.CASH, payment.cash_asset, net, LedgerEntryType.DIVIDEND, payment.instrument_id, None, None),
            (payment.account, LedgerBook.DIVIDEND_INCOME, payment.cash_asset, -payment.gross_amount, LedgerEntryType.DIVIDEND, payment.instrument_id, None, None),
        ]
        if payment.withholding_tax:
            items.append((payment.account, LedgerBook.FEE_EXPENSE, payment.cash_asset, payment.withholding_tax, LedgerEntryType.DIVIDEND, payment.instrument_id, None, None))
        transaction = self._transaction(f"dividend:{payment.payment_id}", payment.timestamp, str(payment.payment_id), tuple(items))
        self.ledger.post(transaction)
        return transaction

    def settle_position(self, account: AccountKey, instrument_id, position_quantity: Decimal, price: Decimal, timestamp: datetime, reference_id: str) -> LedgerTransaction:
        if position_quantity == 0 or price < 0:
            raise ValueError("settlement requires a non-zero position and non-negative price")
        definition = self.catalog.get(instrument_id, timestamp)
        closing_quantity = -position_quantity
        position_asset = AssetId(f"POSITION:{instrument_id.value}")
        multiplier = _premium_multiplier(definition.product_spec)
        cash_amount = -closing_quantity * price * multiplier
        transaction = self._transaction(
            f"settlement:{reference_id}", timestamp, reference_id,
            (
                (account, LedgerBook.POSITION, position_asset, closing_quantity, LedgerEntryType.SETTLEMENT, instrument_id, price, None),
                (account, LedgerBook.CLEARING, position_asset, -closing_quantity, LedgerEntryType.SETTLEMENT, instrument_id, price, None),
                (account, LedgerBook.CASH, definition.quote_asset, cash_amount, LedgerEntryType.SETTLEMENT, instrument_id, price, None),
                (account, LedgerBook.CLEARING, definition.quote_asset, -cash_amount, LedgerEntryType.SETTLEMENT, instrument_id, price, None),
            ),
        )
        self.ledger.post(transaction)
        return transaction

    @staticmethod
    def _transaction(namespace: str, timestamp: datetime, reference_id: str, items) -> LedgerTransaction:
        transaction_id = uuid5(NAMESPACE_URL, namespace)
        entries = tuple(
            LedgerEntry(
                uuid5(NAMESPACE_URL, f"{namespace}:{index}"), transaction_id, timestamp,
                account, book, asset, amount, entry_type, reference_id, instrument_id, unit_price, quantity_multiplier,
            )
            for index, (account, book, asset, amount, entry_type, instrument_id, unit_price, quantity_multiplier) in enumerate(items)
            if amount != 0
        )
        return LedgerTransaction(transaction_id, timestamp, reference_id, entries)


def _premium_multiplier(spec) -> Decimal:
    if isinstance(spec, ListedOptionSpec):
        return spec.multiplier
    if isinstance(spec, CryptoOptionSpec):
        return spec.contract_size
    return Decimal("1")
