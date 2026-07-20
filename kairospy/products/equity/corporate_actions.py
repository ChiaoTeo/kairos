from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from kairospy.accounting.ledger import LedgerService
from kairospy.domain.corporate_action import (
    CashDividendEvent, CorporateActionType, DelistingEvent, InstrumentExchangeEvent,
    SplitEvent, StockDividendEvent, SymbolChangeEvent,
)
from kairospy.domain.execution import DividendPayment
from kairospy.domain.identity import AccountKey, AssetId
from kairospy.domain.ledger import LedgerBook, LedgerEntryType
from kairospy.reference import ReferenceCatalog
from kairospy.reference.access import definition_at


class CorporateActionService:
    def __init__(self, ledger_service: LedgerService) -> None:
        self.ledger_service = ledger_service

    def apply_split(self, account: AccountKey, event: SplitEvent) -> None:
        transaction = self.build_split(account, event)
        if transaction is not None:
            self.ledger_service.ledger.post(transaction)

    def build_split(self, account: AccountKey, event: SplitEvent):
        if event.ratio <= 0 or event.ratio == 1:
            raise ValueError("split ratio must be positive and not one")
        position_asset = AssetId(f"POSITION:{event.instrument_id.value}")
        current = self.ledger_service.ledger.book_balance(account, LedgerBook.POSITION, position_asset)
        if current == 0:
            return None
        delta = current * event.ratio - current
        namespace = f"corporate-action:{event.action_id}"
        return self.ledger_service._transaction(
            namespace, event.effective_at, str(event.action_id),
            ((account, LedgerBook.POSITION, position_asset, delta, LedgerEntryType.CORPORATE_ACTION, event.instrument_id, None, event.ratio),
             (account, LedgerBook.CLEARING, position_asset, -delta, LedgerEntryType.CORPORATE_ACTION, event.instrument_id, None, event.ratio)),
        )

    def apply_dividend(self, account: AccountKey, event: CashDividendEvent) -> None:
        position_asset = AssetId(f"POSITION:{event.instrument_id.value}")
        shares = self.ledger_service.ledger.book_balance(account, LedgerBook.POSITION, position_asset)
        if shares <= 0:
            return
        gross = shares * event.amount_per_share
        self.ledger_service.dividend(DividendPayment(
            event.action_id, event.pay_date, account, event.instrument_id, event.cash_asset,
            gross, gross * event.withholding_rate,
        ))

    def apply_stock_dividend(self, account: AccountKey, event: StockDividendEvent) -> None:
        if event.shares_per_share <= 0:
            raise ValueError("stock dividend ratio must be positive")
        self.apply_split(account, SplitEvent(
            event.action_id, event.instrument_id, event.effective_at,
            Decimal("1") + event.shares_per_share,
        ))

    def apply_exchange(self, account: AccountKey, event: InstrumentExchangeEvent) -> None:
        transaction = self.build_exchange(account, event)
        if transaction is not None:
            self.ledger_service.ledger.post(transaction)

    def build_exchange(self, account: AccountKey, event: InstrumentExchangeEvent):
        if event.action_type not in {CorporateActionType.MERGER, CorporateActionType.SPINOFF}:
            raise ValueError("instrument exchange must be a merger or spinoff")
        if event.target_shares_per_source_share <= 0:
            raise ValueError("exchange ratio must be positive")
        source_asset = AssetId(f"POSITION:{event.source_instrument_id.value}")
        target_asset = AssetId(f"POSITION:{event.target_instrument_id.value}")
        source_quantity = self.ledger_service.ledger.book_balance(account, LedgerBook.POSITION, source_asset)
        if source_quantity <= 0:
            return None
        target_quantity = source_quantity * event.target_shares_per_source_share
        source_delta = -source_quantity if event.action_type is CorporateActionType.MERGER else Decimal("0")
        items = []
        if source_delta:
            items.extend((
                (account, LedgerBook.POSITION, source_asset, source_delta, LedgerEntryType.CORPORATE_ACTION, event.source_instrument_id, None, None),
                (account, LedgerBook.CLEARING, source_asset, -source_delta, LedgerEntryType.CORPORATE_ACTION, event.source_instrument_id, None, None),
            ))
        items.extend((
            (account, LedgerBook.POSITION, target_asset, target_quantity, LedgerEntryType.CORPORATE_ACTION, event.target_instrument_id, None, None),
            (account, LedgerBook.CLEARING, target_asset, -target_quantity, LedgerEntryType.CORPORATE_ACTION, event.target_instrument_id, None, None),
        ))
        return self.ledger_service._transaction(
            f"corporate-action:{event.action_id}", event.effective_at, str(event.action_id), tuple(items),
        )

    def apply_symbol_change(self, event: SymbolChangeEvent) -> None:
        current = definition_at(self.ledger_service.catalog, event.instrument_id, event.effective_at)
        catalog = self.ledger_service.catalog
        catalog.instruments.supersede(replace(current, display_name=event.new_symbol, effective_from=event.effective_at, effective_to=None), event.effective_at)
        for listing in catalog.active_listings(event.instrument_id, event.effective_at):
            catalog.listings.supersede(replace(listing, trading_symbol=event.new_external_symbol, effective_from=event.effective_at, effective_to=None), event.effective_at)

    def apply_delisting(self, event: DelistingEvent) -> None:
        for listing in self.ledger_service.catalog.active_listings(event.instrument_id, event.effective_at):
            self.ledger_service.catalog.listings.end(listing.listing_id, event.effective_at)
