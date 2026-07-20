from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from kairos.accounting.ledger import LedgerService
from kairos.domain.identity import AccountKey, AssetId, InstrumentId
from kairos.domain.ledger import LedgerBook, LedgerEntryType
from kairos.domain.product import ListedOptionSpec, OptionRight, SettlementType
from kairos.reference import ReferenceCatalog, SettlementMethod, SettlementTermsDefinition
from kairos.reference.access import contract_spec, definition_at, trade_cash_asset
from kairos.lifecycle import SettlementResolver


class PhysicalOptionEventType(StrEnum):
    EXERCISE = "exercise"
    ASSIGNMENT = "assignment"
    EXPIRATION = "expiration"


@dataclass(frozen=True, slots=True)
class PhysicalOptionEvent:
    event_id: UUID
    event_type: PhysicalOptionEventType
    account: AccountKey
    option_id: InstrumentId
    contracts: Decimal
    timestamp: datetime
    underlying_price: Decimal | None = None


class OptionLifecycleService:
    def __init__(self, ledger_service: LedgerService) -> None:
        self.ledger_service = ledger_service

    def apply(self, event: PhysicalOptionEvent) -> None:
        definition = definition_at(self.ledger_service.catalog, event.option_id, event.timestamp)
        spec = contract_spec(definition)
        physical = spec.settlement_type is SettlementType.PHYSICAL if isinstance(spec, ListedOptionSpec) else False
        if definition.settlement_terms_id is not None:
            physical = self.ledger_service.catalog.settlements.get(definition.settlement_terms_id, event.timestamp).terms.method is SettlementMethod.PHYSICAL
        if not isinstance(spec, ListedOptionSpec) or not physical:
            raise ValueError("physical option event requires physically settled listed option")
        option_asset = AssetId(f"POSITION:{event.option_id.value}")
        current = self.ledger_service.ledger.book_balance(event.account, LedgerBook.POSITION, option_asset)
        required_sign = 1 if event.event_type is PhysicalOptionEventType.EXERCISE else -1
        if event.event_type is not PhysicalOptionEventType.EXPIRATION and current * required_sign <= 0:
            raise ValueError("option position is incompatible with exercise/assignment")
        close_contracts = min(abs(current), event.contracts)
        if close_contracts <= 0:
            raise ValueError("no option position to close")
        close_quantity = -close_contracts if current > 0 else close_contracts
        items = [
            (event.account, LedgerBook.POSITION, option_asset, close_quantity, _entry_type(event.event_type), event.option_id, Decimal("0"), None),
            (event.account, LedgerBook.CLEARING, option_asset, -close_quantity, _entry_type(event.event_type), event.option_id, Decimal("0"), None),
        ]
        if event.event_type is not PhysicalOptionEventType.EXPIRATION:
            signed_contracts = close_contracts if event.event_type is PhysicalOptionEventType.EXERCISE else -close_contracts
            resolution = SettlementResolver(self.ledger_service.catalog).resolve(event.option_id, signed_contracts, event.underlying_price if event.underlying_price is not None else spec.strike, event.timestamp)
            position_flow = resolution.position_flows[0]
            underlying_quantity = position_flow.quantity
            underlying_id = position_flow.instrument_id
            cash_flow = next(item for item in resolution.flows if item.asset_id == trade_cash_asset(self.ledger_service.catalog, definition, event.timestamp))
            cash_amount = cash_flow.amount
            cash_asset = cash_flow.asset_id
            underlying_asset = AssetId(f"POSITION:{underlying_id.value}")
            items.extend((
                (event.account, LedgerBook.POSITION, underlying_asset, underlying_quantity, _entry_type(event.event_type), underlying_id, spec.strike, None),
                (event.account, LedgerBook.CLEARING, underlying_asset, -underlying_quantity, _entry_type(event.event_type), underlying_id, spec.strike, None),
                (event.account, LedgerBook.CASH, cash_asset, cash_amount, _entry_type(event.event_type), event.option_id, spec.strike, None),
                (event.account, LedgerBook.CLEARING, cash_asset, -cash_amount, _entry_type(event.event_type), event.option_id, spec.strike, None),
            ))
        transaction = self.ledger_service._transaction(
            f"option-lifecycle:{event.event_id}", event.timestamp, str(event.event_id), tuple(items)
        )
        self.ledger_service.ledger.post(transaction)

    def expire(self, account: AccountKey, option_id: InstrumentId, underlying_price: Decimal, timestamp: datetime) -> None:
        definition = definition_at(self.ledger_service.catalog, option_id, timestamp)
        spec = contract_spec(definition)
        if not isinstance(spec, ListedOptionSpec):
            raise ValueError("expiration requires ListedOptionSpec")
        option_asset = AssetId(f"POSITION:{option_id.value}")
        quantity = self.ledger_service.ledger.book_balance(account, LedgerBook.POSITION, option_asset)
        if quantity == 0:
            return
        intrinsic = max(Decimal("0"), underlying_price - spec.strike) if spec.right is OptionRight.CALL else max(Decimal("0"), spec.strike - underlying_price)
        if spec.settlement_type is SettlementType.PHYSICAL and intrinsic >= spec.exercise_threshold:
            event_type = PhysicalOptionEventType.EXERCISE if quantity > 0 else PhysicalOptionEventType.ASSIGNMENT
        else:
            event_type = PhysicalOptionEventType.EXPIRATION
        self.apply(PhysicalOptionEvent(
            uuid5(NAMESPACE_URL, f"expiration:{account.value}:{option_id}:{timestamp.isoformat()}"),
            event_type, account, option_id, abs(quantity), timestamp, underlying_price,
        ))

    def adjust_contract(self, option_id: InstrumentId, effective_at: datetime, *, strike: Decimal, multiplier: Decimal, symbol: str) -> None:
        current = definition_at(self.ledger_service.catalog, option_id, effective_at)
        spec = contract_spec(current)
        if not isinstance(spec, ListedOptionSpec) or strike <= 0 or multiplier <= 0:
            raise ValueError("adjusted option requires positive strike and multiplier")
        catalog = self.ledger_service.catalog
        replacement = replace(current, contract_spec=replace(spec, strike=strike, multiplier=multiplier), display_name=symbol, effective_from=effective_at, effective_to=None)
        catalog.instruments.supersede(replacement, effective_at)
        for listing in catalog.active_listings(option_id, effective_at):
            catalog.listings.supersede(replace(listing, trading_symbol=symbol, effective_from=effective_at, effective_to=None), effective_at)
        if current.settlement_terms_id is not None:
            settlement = catalog.settlements.get(current.settlement_terms_id, effective_at)
            terms = settlement.terms
            if terms.deliverables:
                deliverables = tuple(replace(item, quantity=item.quantity * multiplier / spec.multiplier) for item in terms.deliverables)
                catalog.settlements.supersede(SettlementTermsDefinition(current.settlement_terms_id, replace(terms, deliverables=deliverables), effective_at), effective_at)


def _entry_type(event_type):
    return {
        PhysicalOptionEventType.EXERCISE: LedgerEntryType.EXERCISE,
        PhysicalOptionEventType.ASSIGNMENT: LedgerEntryType.ASSIGNMENT,
        PhysicalOptionEventType.EXPIRATION: LedgerEntryType.SETTLEMENT,
    }[event_type]
