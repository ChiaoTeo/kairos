from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from kairospy.domain.reference import AccountId, BrokerId, InstrumentId


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


class AccountBookKind(StrEnum):
    DEFAULT = "default"
    SPOT = "spot"
    EQUITY = "equity"
    CROSS_MARGIN = "cross_margin"
    ISOLATED_MARGIN = "isolated_margin"
    USD_M_FUTURES = "usd_m_futures"
    COIN_M_FUTURES = "coin_m_futures"
    FUNDING = "funding"
    EARN = "earn"
    PORTFOLIO_MARGIN = "portfolio_margin"


@dataclass(frozen=True, slots=True, order=True)
class AccountIdentity:
    broker: BrokerId | str
    account_id: AccountId | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "broker", _id(self.broker, BrokerId, "broker"))
        object.__setattr__(self, "account_id", _id(self.account_id, AccountId, "account_id"))

    @property
    def value(self) -> str:
        return f"{self.broker}:{self.account_id}"


@dataclass(frozen=True, slots=True, order=True, init=False)
class AccountBookRef:
    identity: AccountIdentity
    book: AccountBookKind | str = AccountBookKind.DEFAULT
    qualifier: str = ""

    def __init__(
        self,
        broker: BrokerId | str | AccountIdentity,
        account_id: AccountId | str | None = None,
        book: AccountBookKind | str = AccountBookKind.DEFAULT,
        qualifier: str = "",
        *,
        segment: str | None = None,
    ) -> None:
        if isinstance(broker, AccountIdentity):
            if account_id is not None:
                raise ValueError("account_id cannot be supplied with AccountIdentity")
            identity = broker
        else:
            if account_id is None:
                raise ValueError("account_id is required")
            identity = AccountIdentity(broker, account_id)
        selected_book = book if segment is None else segment
        normalized_book = _book(selected_book)
        normalized_qualifier = str(qualifier).strip()
        if qualifier and not normalized_qualifier:
            raise ValueError("account book qualifier cannot be blank")
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "book", normalized_book)
        object.__setattr__(self, "qualifier", normalized_qualifier)

    @property
    def broker(self) -> BrokerId | str:
        return self.identity.broker

    @property
    def account_id(self) -> AccountId | str:
        return self.identity.account_id

    @property
    def segment(self) -> str:
        return "" if self.book == AccountBookKind.DEFAULT and not self.qualifier else self.book_key

    @property
    def book_key(self) -> str:
        parts = [str(self.book)]
        if self.qualifier:
            parts.append(self.qualifier)
        return ":".join(parts)

    @property
    def value(self) -> str:
        suffix = "" if not self.segment else f":{self.segment}"
        return f"{self.identity.value}{suffix}"


@dataclass(frozen=True, slots=True, init=False)
class AccountContext:
    book: AccountBookRef
    environment: Environment

    def __init__(self, book: AccountBookRef, environment: Environment) -> None:
        if book is None:
            raise ValueError("account book is required")
        object.__setattr__(self, "book", book)
        object.__setattr__(self, "environment", environment)

    @property
    def identity(self) -> AccountIdentity:
        return self.book.identity

    @property
    def value(self) -> str:
        return f"{self.environment}:{self.book.value}"


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
class LiabilitySnapshot:
    currency: str
    principal: Decimal
    source: AccountSource
    interest: Decimal = Decimal("0")
    instrument_id: InstrumentId | str | None = None

    def __post_init__(self) -> None:
        if not self.currency.strip():
            raise ValueError("liability currency cannot be empty")
        if self.principal < 0 or self.interest < 0:
            raise ValueError("liability values cannot be negative")
        object.__setattr__(self, "instrument_id", None if self.instrument_id is None else _id(self.instrument_id, InstrumentId, "instrument_id"))


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
    liabilities: tuple[LiabilitySnapshot, ...] = ()
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


@dataclass(frozen=True, slots=True)
class AccountCapability:
    book: AccountBookRef
    can_trade: bool = False
    can_hold_cash: bool = True
    can_hold_position: bool = False
    can_borrow: bool = False
    can_transfer_in: bool = True
    can_transfer_out: bool = True
    supported_order_types: tuple[str, ...] = ()
    settlement_currencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountFeeSchedule:
    book: AccountBookRef
    maker: Decimal
    taker: Decimal
    source: str = "configured"
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "maker", Decimal(str(self.maker)))
        object.__setattr__(self, "taker", Decimal(str(self.taker)))
        if self.maker < 0 or self.taker < 0:
            raise ValueError("account fee rates cannot be negative")
        object.__setattr__(self, "source", _required_text(self.source, "account fee source"))
        if self.updated_at is not None and self.updated_at.tzinfo is None:
            raise ValueError("account fee timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AccountAlias:
    key: str
    book: AccountBookRef
    role: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _required_text(self.key, "account alias key"))
        if self.role and not self.role.strip():
            raise ValueError("account alias role cannot be blank")


@dataclass(frozen=True, slots=True)
class AccountDirectory:
    aliases: tuple[AccountAlias, ...] = ()

    def __post_init__(self) -> None:
        keys = [item.key for item in self.aliases]
        if len(keys) != len(set(keys)):
            raise ValueError("account alias keys must be unique")

    @classmethod
    def from_books(cls, books: tuple[AccountBookRef, ...]) -> "AccountDirectory":
        return cls(tuple(AccountAlias(_default_alias_key(book), book) for book in books))

    def require(self, key: str) -> AccountBookRef:
        label = _required_text(key, "account alias key")
        for alias in self.aliases:
            if alias.key == label:
                return alias.book
        raise KeyError(f"unknown account alias: {label}")

    def key_for(self, book: AccountBookRef) -> str | None:
        for alias in self.aliases:
            if alias.book == book:
                return alias.key
        return None


def _required_text(value: object, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} cannot be empty")
    return text


def _id(value, id_type, label: str):
    if isinstance(value, id_type):
        return value
    return id_type(_required_text(value, label))


def _book(value: object) -> AccountBookKind | str:
    text = str(value).strip()
    if not text:
        return AccountBookKind.DEFAULT
    try:
        return AccountBookKind(text)
    except ValueError:
        return text


def _default_alias_key(book: AccountBookRef) -> str:
    if book.book != AccountBookKind.DEFAULT:
        return str(book.book)
    return str(book.account_id)


__all__ = [
    "AccountAlias",
    "AccountBalance",
    "AccountBookKind",
    "AccountBookRef",
    "AccountCapability",
    "AccountContext",
    "AccountDirectory",
    "AccountFeeSchedule",
    "AccountIdentity",
    "AccountSnapshot",
    "AccountSource",
    "Environment",
    "LiabilitySnapshot",
    "MarginScope",
    "MarginState",
    "OpenOrderSnapshot",
    "PositionSnapshot",
]
