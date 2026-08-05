from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Mapping, Protocol

if TYPE_CHECKING:
    from kairospy.domain.order import OrderState
from kairospy.domain.views import ViewFieldSchema, ViewSchema

from .model import (
    AccountBalance,
    AccountBookKind,
    AccountBookRef,
    AccountCapability,
    AccountContext,
    AccountFeeSchedule,
    AccountSnapshot,
    AccountSource,
    LiabilitySnapshot,
    MarginState,
    OpenOrderSnapshot,
    PositionSnapshot,
)
from .state import AccountState


@dataclass(frozen=True, slots=True)
class EquityCurvePoint:
    time: datetime
    equity: Decimal
    cash: Decimal
    positions: tuple[tuple[str, Decimal], ...]


class AccountViewSource(Protocol):
    def get(self, key: str, default: object = None) -> object:
        ...

    def require(self, key: str) -> object:
        ...

    def envelopes(self) -> Mapping[str, object]:
        ...


class AccountViewKeys:
    books = "account.books"
    capabilities = "account.capabilities"
    fees = "account.fees"
    market_profiles = "account.market_profiles"
    detail_prefix = "account.detail"
    portfolio_prefix = "account.portfolio"
    current_prefix = "account.current"

    @staticmethod
    def current(context: AccountContext) -> str:
        parts = [
            "account",
            "current",
            context.environment.value,
            context.book.broker,
            context.book.account_id,
        ]
        if context.book.segment:
            parts.extend(context.book.book_key.split(":"))
        return ".".join(_key_part(part) for part in parts)

    @staticmethod
    def detail(context: AccountContext) -> str:
        parts = [
            "account",
            "detail",
            context.environment.value,
            context.book.broker,
            context.book.account_id,
        ]
        if context.book.segment:
            parts.extend(context.book.book_key.split(":"))
        return ".".join(_key_part(part) for part in parts)

    @staticmethod
    def portfolio(context: AccountContext) -> str:
        return ".".join(
            _key_part(part)
            for part in (
                "account",
                "portfolio",
                context.environment.value,
                context.identity.broker,
                context.identity.account_id,
            )
            if part
        )


@dataclass(frozen=True, slots=True)
class AccountBookSummary:
    key: str
    alias: str
    account_alias: str
    account_index: int
    account_key: str
    book_key: str
    environment: str
    broker: str
    account_id: str
    book_kind: str
    book_qualifier: str = ""


@dataclass(frozen=True, slots=True)
class AccountBooksView:
    total_count: int = 0
    books: tuple[AccountBookSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountCapabilitiesView:
    total_count: int = 0
    capabilities: tuple[AccountCapability, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountFeesView:
    total_count: int = 0
    fees: tuple[AccountFeeSchedule, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountMarketProfilesView:
    total_count: int = 0
    profiles: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountCurrentView:
    context: AccountContext
    identity: object | None = None
    book: AccountBookRef | None = None
    book_kind: str = ""
    book_qualifier: str = ""
    event_count: int = 0
    last_event_time: datetime | None = None
    source: AccountSource | str | None = None
    balances: tuple[AccountBalance, ...] = ()
    margins: tuple[MarginState, ...] = ()
    liabilities: tuple[LiabilitySnapshot, ...] = ()
    positions: tuple[PositionSnapshot, ...] = ()
    open_orders: tuple[OpenOrderSnapshot, ...] = ()
    pending_orders: tuple[OrderState, ...] = ()
    stale: bool = False
    cash: Decimal | None = None
    equity: Decimal | None = None
    initial_equity: Decimal | None = None
    net_profit: Decimal | None = None
    total_return: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AccountDetailView:
    context: AccountContext
    identity: object | None = None
    book: AccountBookRef | None = None
    event_count: int = 0
    last_event_time: datetime | None = None
    account_state: AccountState | None = None
    snapshot: AccountSnapshot | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class AccountPortfolioView:
    account_key: str
    environment: str
    broker: str
    account_id: str
    books: tuple[AccountCurrentView, ...] = ()
    balances: tuple[AccountBalance, ...] = ()
    margins: tuple[MarginState, ...] = ()
    liabilities: tuple[LiabilitySnapshot, ...] = ()
    positions: tuple[PositionSnapshot, ...] = ()
    open_orders: tuple[OpenOrderSnapshot, ...] = ()
    cash: Decimal | None = None
    equity: Decimal | None = None
    stale: bool = False
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EquityCurveView:
    account: AccountContext
    points: tuple[EquityCurvePoint, ...] = ()


ACCOUNT_EQUITY_CURVE_SCHEMA = ViewSchema(
    "account.equity_curve",
    "account",
    fields=(
        ViewFieldSchema("account", "account identity", "runtime state", "account ledger"),
        ViewFieldSchema("points", "marked equity curve points", "event time", "market and account state"),
    ),
    mutability="runtime_writable",
    evidence="marked simulated account equity curve",
)


@dataclass(frozen=True, slots=True)
class AccountViewReader:
    source: AccountViewSource

    def account(self, key: str | int) -> "AccountScopeReader":
        return AccountScopeReader(self.source, key)

    def has_account(self, key: str | int) -> bool:
        return any(_account_key_text(key) in _account_match_keys(item) for item in _account_books(self.source))

    def current(self, key: str | None = None) -> object:
        if key is not None:
            return self.source.require(_account_view_key(self.source, key))
        account_keys = _account_current_keys(self.source)
        if not account_keys:
            raise KeyError("no account view is available")
        if len(account_keys) > 1:
            raise ValueError("multiple account views are available; pass an account key")
        return self.source.require(account_keys[0])

    def detail(self, key: str | None = None) -> object:
        current_key = _account_view_key(self.source, key) if key is not None else _single_account_current_key(self.source)
        return self.source.require(_detail_key_from_current_key(current_key))

    def book(self, key: str) -> object:
        return self.current(key)

    def balance(self, currency: str, *, account: str | None = None) -> AccountBalance | None:
        balances = tuple(getattr(self.current(account), "balances", ()) or ())
        return next((item for item in balances if item.currency == currency), None)

    def position(self, instrument: object, *, account: str | None = None) -> PositionSnapshot | None:
        instrument_id = str(instrument)
        positions = tuple(getattr(self.current(account), "positions", ()) or ())
        return next((item for item in positions if str(item.instrument_id) == instrument_id), None)

    def fees(self, *, account: str | None = None) -> tuple[AccountFeeSchedule, ...]:
        view = self.source.get(AccountViewKeys.fees, None)
        schedules = tuple(getattr(view, "fees", ()) or ())
        if account is None:
            return schedules
        current = self.current(account)
        book = getattr(current, "book", None)
        return tuple(item for item in schedules if item.book == book)

    def market_profile(self, account: AccountBookRef, market: object) -> object | None:
        view = self.source.get(AccountViewKeys.market_profiles, None)
        profiles = tuple(getattr(view, "profiles", ()) or ())
        market_id = str(getattr(market, "market_id", market))
        return next(
            (
                item for item in profiles
                if getattr(getattr(item, "account", None), "book", None) == account
                and str(getattr(getattr(item, "market", None), "market_id", "")) == market_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class AccountScopeReader:
    source: AccountViewSource
    account_key: str | int

    def current(self) -> object:
        return self.source.require(_account_view_key_for_account(self.source, self.account_key, None))

    def detail(self, book: object | None = None) -> object:
        current_key = _account_view_key_for_account(self.source, self.account_key, book)
        return self.source.require(_detail_key_from_current_key(current_key))

    def book(self, key: object | None = None) -> "AccountBookScopeReader":
        return AccountBookScopeReader(self.source, self.account_key, key)

    def overview(self) -> object:
        return self.source.require(_portfolio_view_key_for_account(self.source, self.account_key))

    def balance(self, currency: str, *, book: object | None = None) -> AccountBalance | None:
        balances = tuple(getattr(self.source.require(_account_view_key_for_account(self.source, self.account_key, book)), "balances", ()) or ())
        return next((item for item in balances if item.currency == currency), None)

    def position(self, instrument: object, *, book: object | None = None) -> PositionSnapshot | None:
        instrument_id = str(instrument)
        positions = tuple(getattr(self.source.require(_account_view_key_for_account(self.source, self.account_key, book)), "positions", ()) or ())
        return next((item for item in positions if str(item.instrument_id) == instrument_id), None)

    def fees(self, *, book: object | None = None) -> tuple[AccountFeeSchedule, ...]:
        schedules = tuple(getattr(self.source.get(AccountViewKeys.fees, None), "fees", ()) or ())
        if book is None:
            account_text = _account_key_text(self.account_key)
            account_books = tuple(item for item in _account_books(self.source) if account_text in _account_match_keys(item))
            refs = {getattr(self.source.require(str(getattr(item, "key", ""))), "book", None) for item in account_books}
            return tuple(item for item in schedules if item.book in refs)
        account_view = self.source.require(_account_view_key_for_account(self.source, self.account_key, book))
        selected = getattr(account_view, "book", None)
        return tuple(item for item in schedules if item.book == selected)


@dataclass(frozen=True, slots=True)
class AccountBookScopeReader:
    source: AccountViewSource
    account_key: str | int
    book_key: object | None = None

    def current(self) -> object:
        return self.source.require(_account_view_key_for_account(self.source, self.account_key, self.book_key))

    @property
    def detail(self) -> object:
        return self.source.require(_detail_key_from_current_key(_account_view_key_for_account(self.source, self.account_key, self.book_key)))

    @property
    def market(self) -> "AccountMarketCollectionReader":
        return AccountMarketCollectionReader(self.source, self.account_key, self.book_key)

    def fees(self) -> tuple[AccountFeeSchedule, ...]:
        current = self.current()
        selected = getattr(current, "book", None)
        view = self.source.get(AccountViewKeys.fees, None)
        return tuple(item for item in (getattr(view, "fees", ()) or ()) if item.book == selected)


@dataclass(frozen=True, slots=True)
class AccountMarketScopeReader:
    source: AccountViewSource
    account_key: str | int
    book_key: object | None
    market_ref: object

    @property
    def profile(self) -> object | None:
        current = self.source.require(_account_view_key_for_account(self.source, self.account_key, self.book_key))
        book = getattr(current, "book", None)
        return AccountViewReader(self.source).market_profile(book, self.market_ref)

    @property
    def fee(self) -> object | None:
        profile = self.profile
        return None if profile is None else getattr(profile, "fee", None)

    @property
    def detail(self) -> object:
        profile = self.profile
        if profile is None:
            raise KeyError(f"no account market profile is available for {self.market_ref}")
        return profile


@dataclass(frozen=True, slots=True)
class AccountMarketCollectionReader:
    source: AccountViewSource
    account_key: str | int
    book_key: object | None

    def __call__(self, market: object) -> AccountMarketScopeReader:
        return AccountMarketScopeReader(self.source, self.account_key, self.book_key, market)

    def get(self, market: object) -> AccountMarketScopeReader:
        return self(market)


ACCOUNT_BOOKS_SCHEMA = ViewSchema(
    AccountViewKeys.books,
    "account",
    fields=(
        ViewFieldSchema("total_count", "known account book count", "runtime state", "account port"),
        ViewFieldSchema("books", "account book summaries", "runtime state", "account port"),
    ),
    mutability="runtime_writable",
    evidence="runtime account book index",
)

ACCOUNT_CAPABILITIES_SCHEMA = ViewSchema(
    AccountViewKeys.capabilities,
    "account",
    fields=(
        ViewFieldSchema("total_count", "known account capability count", "runtime state", "account port"),
        ViewFieldSchema("capabilities", "account book capabilities", "runtime state", "account port"),
    ),
    mutability="runtime_writable",
    evidence="runtime account capability index",
)


ACCOUNT_FEES_SCHEMA = ViewSchema(
    AccountViewKeys.fees,
    "account",
    fields=(
        ViewFieldSchema("total_count", "known account fee schedule count", "runtime state", "account port"),
        ViewFieldSchema("fees", "account book maker/taker fee schedules", "runtime state", "account port"),
    ),
    mutability="runtime_writable",
    evidence="runtime account fee schedule index",
)


ACCOUNT_MARKET_PROFILES_SCHEMA = ViewSchema(
    AccountViewKeys.market_profiles,
    "account",
    fields=(
        ViewFieldSchema("total_count", "known account market profile count", "runtime state", "account port"),
        ViewFieldSchema("profiles", "account and market fee/margin profiles", "runtime state", "account port"),
    ),
    mutability="runtime_writable",
    evidence="runtime account market profile index",
)


def account_current_schema(key: str) -> ViewSchema:
    return ViewSchema(
        key,
        "account",
        fields=(
            ViewFieldSchema("context", "account identity and environment", "runtime account event", "account port"),
            ViewFieldSchema("identity", "account authentication identity", "runtime account event", "account port"),
            ViewFieldSchema("book", "account book identity", "runtime account event", "account port"),
            ViewFieldSchema("book_kind", "account book or wallet kind", "runtime account event", "account port"),
            ViewFieldSchema("book_qualifier", "account book qualifier", "runtime account event", "account port"),
            ViewFieldSchema("event_count", "consumed account event count", "runtime sequence", "account view state"),
            ViewFieldSchema("last_event_time", "latest account event time", "event time", "account event"),
            ViewFieldSchema("source", "account data source", "event time", "account state or snapshot"),
            ViewFieldSchema("balances", "account balances", "event time", "account state or snapshot"),
            ViewFieldSchema("margins", "account margin states", "event time", "account state or snapshot"),
            ViewFieldSchema("liabilities", "account liabilities and borrow states", "event time", "account state or snapshot"),
            ViewFieldSchema("positions", "account positions", "event time", "account state or snapshot"),
            ViewFieldSchema("open_orders", "venue open orders", "event time", "account state or snapshot"),
            ViewFieldSchema("pending_orders", "local active order states", "runtime state", "account event"),
            ViewFieldSchema("stale", "account state staleness flag", "event time", "account state"),
            ViewFieldSchema("cash", "cash in selected equity currency", "event time", "account balances"),
            ViewFieldSchema("equity", "marked account equity", "event time", "account event or balances"),
            ViewFieldSchema("initial_equity", "first or configured account equity baseline", "launch baseline", "account view state"),
            ViewFieldSchema("net_profit", "equity minus baseline", "event time", "account view state"),
            ViewFieldSchema("total_return", "net profit divided by baseline", "event time", "account view state"),
        ),
        mutability="runtime_writable",
        evidence="runtime account view state",
    )


def account_detail_schema(key: str) -> ViewSchema:
    return ViewSchema(
        key,
        "account",
        fields=(
            ViewFieldSchema("context", "account identity and environment", "runtime account event", "account port"),
            ViewFieldSchema("identity", "account authentication identity", "runtime account event", "account port"),
            ViewFieldSchema("book", "account book identity", "runtime account event", "account port"),
            ViewFieldSchema("event_count", "consumed account event count", "runtime sequence", "account view state"),
            ViewFieldSchema("last_event_time", "latest account event time", "event time", "account event"),
            ViewFieldSchema("account_state", "complete account state", "event time", "account port"),
            ViewFieldSchema("snapshot", "latest account snapshot", "event time", "account port"),
            ViewFieldSchema("metadata", "non-domain account event metadata", "event time", "account event"),
        ),
        mutability="runtime_writable",
        evidence="runtime account detail view state",
    )


def account_portfolio_schema(key: str) -> ViewSchema:
    return ViewSchema(
        key,
        "account",
        fields=(
            ViewFieldSchema("account_key", "account identity key", "runtime state", "account current views"),
            ViewFieldSchema("books", "included account book views", "runtime state", "account current views"),
            ViewFieldSchema("balances", "aggregated account balances", "runtime state", "account current views"),
            ViewFieldSchema("positions", "aggregated account positions", "runtime state", "account current views"),
            ViewFieldSchema("cash", "aggregated cash when currency is unambiguous", "runtime state", "account current views"),
            ViewFieldSchema("equity", "aggregated equity when currency is unambiguous", "runtime state", "account current views"),
            ViewFieldSchema("stale", "whether any included book is stale", "runtime state", "account current views"),
        ),
        mutability="runtime_writable",
        evidence="runtime account portfolio projection",
    )


def _account_current_keys(source: AccountViewSource) -> tuple[str, ...]:
    return tuple(key for key in source.envelopes() if key.startswith(AccountViewKeys.current_prefix + "."))


def _single_account_current_key(source: AccountViewSource) -> str:
    keys = _account_current_keys(source)
    if not keys:
        raise KeyError("no account view is available")
    if len(keys) > 1:
        raise ValueError("multiple account views are available; pass an account key")
    return keys[0]


def _detail_key_from_current_key(key: str) -> str:
    if key.startswith(AccountViewKeys.detail_prefix + "."):
        return key
    if key.startswith(AccountViewKeys.current_prefix + "."):
        return AccountViewKeys.detail_prefix + key.removeprefix(AccountViewKeys.current_prefix)
    return key


def _account_view_key(source: AccountViewSource, key: str) -> str:
    if key.startswith(AccountViewKeys.detail_prefix + "."):
        return AccountViewKeys.current_prefix + key.removeprefix(AccountViewKeys.detail_prefix)
    if key.startswith(AccountViewKeys.current_prefix + "."):
        return key
    books = _account_books(source)
    for item in books:
        item_key = str(getattr(item, "key", ""))
        if key in {
            item_key,
            str(getattr(item, "alias", "")),
            _book_identity_key(item),
        }:
            return item_key
    book_matches = tuple(
        str(getattr(item, "key", ""))
        for item in books
        if key in {str(getattr(item, "book_kind", "")), str(getattr(item, "book_qualifier", ""))}
    )
    if len(book_matches) == 1:
        return book_matches[0]
    if len(book_matches) > 1:
        raise ValueError(f"multiple account books match account key: {key}; use an account alias or full account book key")
    matches = tuple(view_key for view_key in _account_current_keys(source) if view_key.endswith(f".{key}"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"multiple account views match account key: {key}")
    return key


def _account_view_key_for_account(source: AccountViewSource, account_key: str | int, book_key: object | None) -> str:
    account_text = _account_key_text(account_key)
    account_matches = tuple(item for item in _account_books(source) if account_text in _account_match_keys(item))
    if not account_matches:
        raise KeyError(f"unknown account: {account_text}")
    if book_key is None:
        if len(account_matches) == 1:
            return str(getattr(account_matches[0], "key", ""))
        raise ValueError(f"multiple books are available for account: {account_text}; pass a book key")
    book_text = _book_key_text(book_key)
    book_matches = tuple(item for item in account_matches if book_text in _book_match_keys(item))
    if len(book_matches) == 1:
        return str(getattr(book_matches[0], "key", ""))
    if len(book_matches) > 1:
        raise ValueError(f"multiple books match account {account_text!r} and book {book_text!r}")
    raise KeyError(f"unknown account book: {account_text}.{book_text}")


def _portfolio_view_key_for_account(source: AccountViewSource, account_key: str | int) -> str:
    account_text = _account_key_text(account_key)
    account_matches = tuple(item for item in _account_books(source) if account_text in _account_match_keys(item))
    if not account_matches:
        raise KeyError(f"unknown account: {account_text}")
    keys = {
        ".".join(
            _key_part(part)
            for part in (
                "account",
                "portfolio",
                getattr(item, "environment", ""),
                getattr(item, "broker", ""),
                getattr(item, "account_id", ""),
            )
            if part
        )
        for item in account_matches
    }
    if len(keys) == 1:
        return next(iter(keys))
    raise ValueError(f"multiple account portfolio views match account: {account_text}")


def account_current_view_key(context: AccountContext) -> str:
    return AccountViewKeys.current(context)


def account_detail_view_key(context: AccountContext) -> str:
    return AccountViewKeys.detail(context)


def _account_books(source: AccountViewSource) -> tuple[object, ...]:
    books_view = source.get(AccountViewKeys.books, None)
    return tuple(getattr(books_view, "books", ()) or ())


def _account_match_keys(item: object) -> set[str]:
    return {
        str(getattr(item, "account_index", "")),
        str(getattr(item, "account_alias", "")),
        str(getattr(item, "account_key", "")),
        ".".join(_key_part(part) for part in (getattr(item, "broker", ""), getattr(item, "account_id", "")) if part),
        str(getattr(item, "account_id", "")),
    }


def _book_match_keys(item: object) -> set[str]:
    return {
        str(getattr(item, "book_key", "")),
        str(getattr(item, "book_kind", "")),
        str(getattr(item, "book_qualifier", "")),
        str(getattr(item, "alias", "")),
        _book_identity_key(item),
        str(getattr(item, "key", "")),
    }


def _account_key_text(value: str | int) -> str:
    if isinstance(value, bool):
        raise ValueError("account key must be an alias or integer index")
    return str(value)


def _book_key_text(value: object) -> str:
    if isinstance(value, AccountBookKind):
        return value.value
    return str(value)


def _book_identity_key(item: object) -> str:
    parts = [
        str(getattr(item, "broker", "")),
        str(getattr(item, "account_id", "")),
        str(getattr(item, "book_kind", "")),
        str(getattr(item, "book_qualifier", "")),
    ]
    return ".".join(_key_part(part) for part in parts if part)


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in ("".join(character if character.isalnum() else "_" for character in text)).split("_") if part)


__all__ = [
    "ACCOUNT_BOOKS_SCHEMA",
    "ACCOUNT_CAPABILITIES_SCHEMA",
    "ACCOUNT_FEES_SCHEMA",
    "ACCOUNT_MARKET_PROFILES_SCHEMA",
    "AccountBookSummary",
    "AccountBooksView",
    "AccountCapabilitiesView",
    "AccountCurrentView",
    "AccountDetailView",
    "ACCOUNT_EQUITY_CURVE_SCHEMA",
    "EquityCurveView",
    "EquityCurvePoint",
    "AccountFeesView",
    "AccountMarketProfilesView",
    "AccountPortfolioView",
    "AccountScopeReader",
    "AccountBookScopeReader",
    "AccountMarketScopeReader",
    "AccountMarketCollectionReader",
    "AccountViewReader",
    "AccountViewKeys",
    "AccountViewSource",
    "account_current_view_key",
    "account_current_schema",
    "account_detail_view_key",
    "account_detail_schema",
    "account_portfolio_schema",
]
