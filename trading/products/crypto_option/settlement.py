from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from trading.accounting.ledger import LedgerService
from trading.domain.identity import AccountKey, AssetId, InstrumentId
from trading.domain.ledger import LedgerBook, LedgerEntryType
from trading.domain.product import CryptoOptionSpec, OptionRight


class CryptoOptionSettlementService:
    def __init__(self, ledger_service: LedgerService) -> None:
        self.ledger_service = ledger_service

    def settle(self, account: AccountKey, instrument_id: InstrumentId, settlement_price: Decimal, timestamp: datetime):
        definition = self.ledger_service.catalog.get(instrument_id, timestamp)
        spec = definition.product_spec
        if not isinstance(spec, CryptoOptionSpec):
            raise ValueError("crypto option settlement requires CryptoOptionSpec")
        position_asset = AssetId(f"POSITION:{instrument_id.value}")
        quantity = self.ledger_service.ledger.book_balance(account, LedgerBook.POSITION, position_asset)
        if quantity == 0:
            return None
        intrinsic = max(Decimal("0"), settlement_price - spec.strike) if spec.right is OptionRight.CALL else max(Decimal("0"), spec.strike - settlement_price)
        cash = quantity * intrinsic * spec.contract_size
        namespace = f"crypto-option-settlement:{account.value}:{instrument_id}:{timestamp.isoformat()}"
        items = [
            (account, LedgerBook.POSITION, position_asset, -quantity, LedgerEntryType.SETTLEMENT, instrument_id, intrinsic, None),
            (account, LedgerBook.CLEARING, position_asset, quantity, LedgerEntryType.SETTLEMENT, instrument_id, intrinsic, None),
        ]
        if cash:
            items.extend((
                (account, LedgerBook.CASH, spec.settlement_asset, cash, LedgerEntryType.SETTLEMENT, instrument_id, settlement_price, None),
                (account, LedgerBook.REALIZED_PNL, spec.settlement_asset, -cash, LedgerEntryType.SETTLEMENT, instrument_id, settlement_price, None),
            ))
        transaction = self.ledger_service._transaction(namespace, timestamp, namespace, tuple(items))
        self.ledger_service.ledger.post(transaction)
        return transaction
