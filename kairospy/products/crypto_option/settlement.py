from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from kairospy.portfolio.accounting.ledger import LedgerService
from kairospy.execution.ingestion import DurableAccountingIngestionService
from kairospy.identity import AccountRef, AssetId, InstrumentId
from kairospy.portfolio.ledger import LedgerBook, LedgerEntryType
from kairospy.reference.contracts import CryptoOptionSpec, OptionRight
from kairospy.reference.access import contract_spec, definition_at


@dataclass(frozen=True, slots=True)
class CryptoOptionSettlementEvent:
    account: AccountRef
    instrument_id: InstrumentId
    settlement_price: Decimal
    timestamp: datetime


class CryptoOptionSettlementService:
    def __init__(self, ledger_service: LedgerService) -> None:
        self.ledger_service = ledger_service

    def settle(self, account: AccountRef, instrument_id: InstrumentId, settlement_price: Decimal, timestamp: datetime):
        transaction = self.build_settlement(account, instrument_id, settlement_price, timestamp)
        if transaction is None:
            return None
        self.ledger_service.ledger.post(transaction)
        return transaction

    def build_settlement(self, account: AccountRef, instrument_id: InstrumentId, settlement_price: Decimal, timestamp: datetime):
        definition = definition_at(self.ledger_service.catalog, instrument_id, timestamp)
        spec = contract_spec(definition)
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
        return transaction


class DurableCryptoOptionSettlementService:
    def __init__(
        self,
        settlement_service: CryptoOptionSettlementService,
        ingestion: DurableAccountingIngestionService,
    ) -> None:
        self.settlement_service = settlement_service
        self.ingestion = ingestion

    def settle(self, account: AccountRef, instrument_id: InstrumentId, settlement_price: Decimal, timestamp: datetime):
        event = CryptoOptionSettlementEvent(account, instrument_id, settlement_price, timestamp)
        transaction = self.settlement_service.build_settlement(
            account, instrument_id, settlement_price, timestamp,
        )
        if transaction is None:
            return None
        external_key = f"crypto-option:settlement:{account.value}:{instrument_id.value}:{timestamp.isoformat()}"
        return self.ingestion.ingest_settlement(
            event,
            transaction,
            external_key=external_key,
            occurred_at=timestamp,
        )
