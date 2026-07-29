from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from kairospy.core.reference import AccountId, BrokerId, InstrumentId


class Environment(StrEnum):
    BACKTEST = "backtest"
    SIMULATION = "simulation"
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


class AccountSource(StrEnum):
    LEDGER = "ledger"
    MODEL = "model"
    SIMULATED = "simulated"
    VENUE = "venue"
    MIXED = "mixed"
    STALE = "stale"


class MarginScope(StrEnum):
    ACCOUNT = "account"
    INSTRUMENT = "instrument"
    POSITION = "position"


@dataclass(frozen=True, slots=True, order=True)
class AccountRef:
    broker: BrokerId | str
    account_id: AccountId | str
    segment: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "broker", _id(self.broker, BrokerId, "broker"))
        object.__setattr__(self, "account_id", _id(self.account_id, AccountId, "account_id"))
        if self.segment and not self.segment.strip():
            raise ValueError("account segment cannot be blank")

    @property
    def value(self) -> str:
        suffix = f":{self.segment}" if self.segment else ""
        return f"{self.broker}:{self.account_id}{suffix}"


@dataclass(frozen=True, slots=True)
class AccountContext:
    account: AccountRef
    environment: Environment

    @property
    def value(self) -> str:
        return f"{self.environment}:{self.account.value}"


@dataclass(frozen=True, slots=True)
class AccountBalance:
    currency: str
    total: Decimal
    free: Decimal
    locked: Decimal
    source: AccountSource

    def __post_init__(self) -> None:
        if not self.currency.strip():
            raise ValueError("balance currency cannot be empty")
        if self.total != self.free + self.locked:
            raise ValueError("account balance must satisfy total == free + locked")
        if self.locked < 0:
            raise ValueError("locked balance cannot be negative")

    @classmethod
    def from_total_locked(
        cls,
        currency: str,
        total: Decimal,
        locked: Decimal,
        *,
        source: AccountSource,
    ) -> "AccountBalance":
        return cls(currency, total, total - locked, locked, source)

    @classmethod
    def from_free_locked(
        cls,
        currency: str,
        free: Decimal,
        locked: Decimal,
        *,
        source: AccountSource,
    ) -> "AccountBalance":
        return cls(currency, free + locked, free, locked, source)


@dataclass(frozen=True, slots=True)
class MarginState:
    currency: str
    initial: Decimal
    maintenance: Decimal
    source: AccountSource
    scope: MarginScope = MarginScope.ACCOUNT
    instrument_id: InstrumentId | str | None = None
    available: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.currency.strip():
            raise ValueError("margin currency cannot be empty")
        if self.initial < 0 or self.maintenance < 0:
            raise ValueError("margin values cannot be negative")
        if self.available is not None and self.available < 0:
            raise ValueError("available margin cannot be negative")
        if self.scope is not MarginScope.ACCOUNT and not self.instrument_id:
            raise ValueError("instrument or position margin requires instrument_id")
        object.__setattr__(self, "instrument_id", None if self.instrument_id is None else _id(self.instrument_id, InstrumentId, "instrument_id"))


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    instrument_id: InstrumentId | str
    quantity: Decimal
    source: AccountSource
    average_price: Decimal | None = None
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    liquidation_price: Decimal | None = None
    margin_currency: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        if self.quantity == 0:
            raise ValueError("zero positions should be omitted")


@dataclass(frozen=True, slots=True)
class OpenOrderSnapshot:
    order_id: str
    instrument_id: InstrumentId | str
    side: str
    quantity: Decimal
    source: AccountSource
    reserved_currency: str | None = None
    reserved_amount: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        if not self.order_id.strip() or not self.side.strip():
            raise ValueError("open order identity fields cannot be empty")
        if self.quantity <= 0:
            raise ValueError("open order quantity must be positive")
        if self.reserved_amount < 0:
            raise ValueError("reserved amount cannot be negative")
        if self.reserved_amount and not self.reserved_currency:
            raise ValueError("reserved currency is required when reserved amount is positive")


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    context: AccountContext
    balances: tuple[AccountBalance, ...]
    margins: tuple[MarginState, ...] = ()
    positions: tuple[PositionSnapshot, ...] = ()
    open_orders: tuple[OpenOrderSnapshot, ...] = ()
    observed_at: datetime | None = None
    source: AccountSource = AccountSource.VENUE
    raw: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        currencies = [balance.currency for balance in self.balances]
        if len(currencies) != len(set(currencies)):
            raise ValueError("account snapshot cannot contain duplicate balance currencies")
        if self.observed_at is not None and self.observed_at.tzinfo is None:
            raise ValueError("account snapshot timestamp must be timezone-aware")


def _required_text(value: object, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} cannot be empty")
    return text


def _id(value, id_type, label: str):
    if isinstance(value, id_type):
        return value
    return id_type(_required_text(value, label))


__all__ = [
    "AccountBalance",
    "AccountContext",
    "AccountRef",
    "AccountSnapshot",
    "AccountSource",
    "Environment",
    "MarginScope",
    "MarginState",
    "OpenOrderSnapshot",
    "PositionSnapshot",
]
