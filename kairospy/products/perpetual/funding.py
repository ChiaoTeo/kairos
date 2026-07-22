from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from kairospy.portfolio.accounting.ledger import LedgerService
from kairospy.portfolio.ledger_events import FundingPayment
from kairospy.identity import AccountRef, InstrumentId
from kairospy.reference.contracts import ContractType, PerpetualSpec
from kairospy.reference.access import contract_spec, definition_at


class FundingEngine:
    def __init__(self, ledger_service: LedgerService) -> None:
        self.ledger_service = ledger_service

    def apply(self, account: AccountRef, instrument_id: InstrumentId, position_quantity: Decimal, mark_price: Decimal, funding_rate: Decimal, timestamp: datetime):
        definition = definition_at(self.ledger_service.catalog, instrument_id, timestamp)
        spec = contract_spec(definition)
        if not isinstance(spec, PerpetualSpec):
            raise ValueError("funding requires a perpetual instrument")
        if position_quantity == 0 or funding_rate == 0:
            return None
        if spec.contract_type is ContractType.INVERSE:
            notional = abs(position_quantity) * spec.contract_size / mark_price
        else:
            notional = abs(position_quantity) * spec.contract_size * mark_price
        direction = Decimal("1") if position_quantity > 0 else Decimal("-1")
        amount = -direction * notional * funding_rate
        payment = FundingPayment(
            uuid5(NAMESPACE_URL, f"funding:{account.value}:{instrument_id}:{timestamp.isoformat()}"),
            timestamp, account, instrument_id, spec.settlement_asset, amount, funding_rate, notional,
        )
        self.ledger_service.funding(payment)
        return payment
