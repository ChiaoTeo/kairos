from __future__ import annotations

from decimal import Decimal

from kairos.accounting.ledger import LedgerService
from kairos.domain.derivative_event import DerivativeEventType, DerivativePositionEvent
from kairos.domain.execution import TradeExecution, TradeSide
from kairos.domain.identity import AssetId
from kairos.domain.ledger import LedgerBook
from kairos.domain.product import FutureSpec, PerpetualSpec
from kairos.reference.access import contract_spec, definition_at


class DerivativeLifecycleService:
    def __init__(self, ledger_service: LedgerService) -> None:
        self.ledger_service = ledger_service

    def apply(self, event: DerivativePositionEvent) -> None:
        definition = definition_at(self.ledger_service.catalog, event.instrument_id, event.timestamp)
        if not isinstance(contract_spec(definition), (FutureSpec, PerpetualSpec)):
            raise ValueError("derivative lifecycle requires future or perpetual")
        position_asset = AssetId(f"POSITION:{event.instrument_id.value}")
        current = self.ledger_service.ledger.book_balance(event.account, LedgerBook.POSITION, position_asset)
        if current == 0 or event.quantity <= 0 or event.quantity > abs(current):
            raise ValueError("derivative event quantity exceeds position")
        if event.event_type not in {
            DerivativeEventType.CONTRACT_EXPIRED, DerivativeEventType.CASH_SETTLED,
            DerivativeEventType.POSITION_LIQUIDATED, DerivativeEventType.AUTO_DELEVERAGED,
        }:
            raise ValueError("unsupported derivative lifecycle event")
        self.ledger_service.trade(TradeExecution(
            event.event_id, event.timestamp, event.account, event.instrument_id,
            TradeSide.SELL if current > 0 else TradeSide.BUY, event.quantity, event.price,
            event.settlement_asset, Decimal("0"), str(event.event_id),
        ))
