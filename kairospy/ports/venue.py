from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from kairospy.trading.capability import ExecutionCapabilities, MarketDataCapabilities, ReferenceCapabilities
from kairospy.trading.execution import TradeExecution, TradeSide
from kairospy.trading.identity import AccountKey, AssetId, InstitutionId, InstrumentId, VenueId
from kairospy.reference.contracts import InstrumentDefinition
from kairospy.trading.market_data import Quote
from kairospy.trading.corporate_action import CashDividendEvent, SplitEvent
from kairospy.trading.order import ExecutionInstructions
from kairospy.trading.product import ProductType
from kairospy.reference.catalog import ReferenceCatalog


class Environment(StrEnum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


class VenueOrderStatus(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OrderRequest:
    internal_order_id: str
    client_order_id: str
    strategy_id: str
    intent_id: str
    correlation_id: str
    account: AccountKey
    instrument_id: InstrumentId
    side: TradeSide
    quantity: Decimal
    instructions: ExecutionInstructions


@dataclass(frozen=True, slots=True)
class OrderAck:
    internal_order_id: str
    client_order_id: str
    strategy_id: str
    intent_id: str
    correlation_id: str
    venue_order_id: str
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class VenueBalance:
    asset: AssetId
    total: Decimal
    available: Decimal = Decimal("0")
    locked: Decimal = Decimal("0")
    borrowed: Decimal = Decimal("0")
    interest: Decimal = Decimal("0")
    collateral: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class ComboLegRequest:
    instrument_id: InstrumentId
    side: TradeSide
    ratio: int


@dataclass(frozen=True, slots=True)
class ComboOrderRequest:
    internal_order_id: str
    client_order_id: str
    strategy_id: str
    intent_id: str
    correlation_id: str
    account: AccountKey
    legs: tuple[ComboLegRequest, ...]
    quantity: Decimal
    instructions: ExecutionInstructions


@dataclass(frozen=True, slots=True)
class AccountState:
    account: AccountKey
    balances: tuple[VenueBalance, ...]
    positions: tuple[tuple[InstrumentId, Decimal], ...]
    open_order_ids: tuple[str, ...]
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class RecoveredExecution:
    external_key: str
    execution: TradeExecution
    fully_filled: bool
    cursor_name: str | None = None
    cursor_value: str | None = None


@dataclass(frozen=True, slots=True)
class VenueOrderRecovery:
    status: VenueOrderStatus
    proof: str
    acknowledgement: OrderAck | None = None
    executions: tuple[RecoveredExecution, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceDataRequest:
    product_type: ProductType
    symbols: tuple[str, ...]


class ReferenceDataPort(Protocol):
    venue_id: VenueId
    capabilities: ReferenceCapabilities
    def sync(self, request: ReferenceDataRequest) -> ReferenceCatalog: ...


class MarketDataPort(Protocol):
    venue_id: VenueId
    capabilities: MarketDataCapabilities
    def snapshot(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[Quote, ...]: ...


class ExecutionPort(Protocol):
    institution_id: InstitutionId
    venue_id: VenueId
    environment: Environment
    capabilities: ExecutionCapabilities
    def place_order(self, request: OrderRequest) -> OrderAck: ...
    def cancel_order(self, account: AccountKey, venue_order_id: str) -> None: ...
    def open_orders(self, account: AccountKey) -> tuple[str, ...]: ...


class ComboExecutionPort(Protocol):
    venue_id: VenueId
    capabilities: ExecutionCapabilities
    def place_combo_order(self, request: ComboOrderRequest) -> OrderAck: ...


class AccountPort(Protocol):
    institution_id: InstitutionId
    venue_id: VenueId
    environment: Environment
    def account_state(self, account: AccountKey) -> AccountState: ...


class OrderRecoveryPort(Protocol):
    institution_id: InstitutionId
    venue_id: VenueId
    environment: Environment
    def recover_order(
        self,
        account: AccountKey,
        request: OrderRequest | ComboOrderRequest,
        venue_order_id: str | None,
    ) -> VenueOrderRecovery: ...


class CorporateActionPort(Protocol):
    venue_id: VenueId
    def corporate_actions(self, instruments: tuple[InstrumentId, ...], start: datetime, end: datetime) -> tuple[CashDividendEvent | SplitEvent, ...]: ...


class FundingSettlementPort(Protocol):
    venue_id: VenueId
    def funding_history(self, account: AccountKey, start: datetime, end: datetime) -> tuple[object, ...]: ...
    def settlement_history(self, account: AccountKey, start: datetime, end: datetime) -> tuple[object, ...]: ...

