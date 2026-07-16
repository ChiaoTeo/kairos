from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from trading.accounting.ledger import LedgerService
from trading.domain.identity import AccountKey, AssetId, InstrumentId
from trading.domain.ledger import LedgerBook, LedgerEntryType
from trading.domain.product import ListedOptionSpec, OptionRight, SettlementType


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
        definition = self.ledger_service.catalog.get(event.option_id, event.timestamp)
        spec = definition.product_spec
        if not isinstance(spec, ListedOptionSpec) or spec.settlement_type is not SettlementType.PHYSICAL:
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
            shares = close_contracts * spec.multiplier
            if spec.right is OptionRight.CALL:
                underlying_quantity = shares if event.event_type is PhysicalOptionEventType.EXERCISE else -shares
            else:
                underlying_quantity = -shares if event.event_type is PhysicalOptionEventType.EXERCISE else shares
            underlying_asset = AssetId(f"POSITION:{spec.underlying.value}")
            cash_amount = -underlying_quantity * spec.strike
            items.extend((
                (event.account, LedgerBook.POSITION, underlying_asset, underlying_quantity, _entry_type(event.event_type), spec.underlying, spec.strike, None),
                (event.account, LedgerBook.CLEARING, underlying_asset, -underlying_quantity, _entry_type(event.event_type), spec.underlying, spec.strike, None),
                (event.account, LedgerBook.CASH, definition.quote_asset, cash_amount, _entry_type(event.event_type), event.option_id, spec.strike, None),
                (event.account, LedgerBook.CLEARING, definition.quote_asset, -cash_amount, _entry_type(event.event_type), event.option_id, spec.strike, None),
            ))
        transaction = self.ledger_service._transaction(
            f"option-lifecycle:{event.event_id}", event.timestamp, str(event.event_id), tuple(items)
        )
        self.ledger_service.ledger.post(transaction)

    def expire(self, account: AccountKey, option_id: InstrumentId, underlying_price: Decimal, timestamp: datetime) -> None:
        definition = self.ledger_service.catalog.get(option_id, timestamp)
        spec = definition.product_spec
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
        current = self.ledger_service.catalog.get(option_id, effective_at)
        spec = current.product_spec
        if not isinstance(spec, ListedOptionSpec) or strike <= 0 or multiplier <= 0:
            raise ValueError("adjusted option requires positive strike and multiplier")
        replacement = replace(
            current, symbol=symbol, product_spec=replace(spec, strike=strike, multiplier=multiplier),
            effective_from=effective_at, effective_to=None, schema_version=current.schema_version + 1,
            listings=tuple(replace(item, symbol=symbol, listed_at=effective_at, delisted_at=None) for item in current.listings),
        )
        self.ledger_service.catalog.supersede(replacement, effective_at)


def _entry_type(event_type):
    return {
        PhysicalOptionEventType.EXERCISE: LedgerEntryType.EXERCISE,
        PhysicalOptionEventType.ASSIGNMENT: LedgerEntryType.ASSIGNMENT,
        PhysicalOptionEventType.EXPIRATION: LedgerEntryType.SETTLEMENT,
    }[event_type]
