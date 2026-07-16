from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from trading.domain.capability import ExecutionCapabilities, MarketDataCapabilities, ReferenceCapabilities
from trading.domain.execution import TradeExecution, TradeSide
from trading.domain.identity import AccountKey, AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition
from trading.domain.market_data import Quote
from trading.domain.corporate_action import CashDividendEvent, SplitEvent
from trading.domain.order import ExecutionInstructions
from trading.domain.product import ProductType


class Environment(StrEnum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


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
class ReferenceDataRequest:
    product_type: ProductType
    symbols: tuple[str, ...]


class ReferenceDataAdapter(Protocol):
    venue_id: VenueId
    capabilities: ReferenceCapabilities
    def sync(self, request: ReferenceDataRequest) -> tuple[InstrumentDefinition, ...]: ...


class MarketDataAdapter(Protocol):
    venue_id: VenueId
    capabilities: MarketDataCapabilities
    def snapshot(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[Quote, ...]: ...


class ExecutionAdapter(Protocol):
    venue_id: VenueId
    environment: Environment
    capabilities: ExecutionCapabilities
    def place_order(self, request: OrderRequest) -> OrderAck: ...
    def cancel_order(self, account: AccountKey, venue_order_id: str) -> None: ...
    def open_orders(self, account: AccountKey) -> tuple[str, ...]: ...


class ComboExecutionAdapter(Protocol):
    venue_id: VenueId
    capabilities: ExecutionCapabilities
    def place_combo_order(self, request: ComboOrderRequest) -> OrderAck: ...


class AccountAdapter(Protocol):
    venue_id: VenueId
    environment: Environment
    def account_state(self, account: AccountKey) -> AccountState: ...


class CorporateActionAdapter(Protocol):
    venue_id: VenueId
    def corporate_actions(self, instruments: tuple[InstrumentId, ...], start: datetime, end: datetime) -> tuple[CashDividendEvent | SplitEvent, ...]: ...


class FundingSettlementAdapter(Protocol):
    venue_id: VenueId
    def funding_history(self, account: AccountKey, start: datetime, end: datetime) -> tuple[object, ...]: ...
    def settlement_history(self, account: AccountKey, start: datetime, end: datetime) -> tuple[object, ...]: ...
