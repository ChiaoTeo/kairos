from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Mapping, Protocol, cast

if TYPE_CHECKING:
    from kairospy.domain.order import OrderState
from kairospy.domain.views import ViewFieldSchema, ViewSchema

from .model import (
    AccountBalance,
    CollateralBalance,
    ExternalAccountIdentity,
    AccountMarketProfile,
    AccountModel,
    AccountSegment,
    AccountCapability,
    AccountRuntimeContext,
    AccountFeeSchedule,
    AccountSnapshot,
    AccountSource,
    AssetCode,
    LiabilitySnapshot,
    MarginState,
    OpenOrderSnapshot,
    PositionSnapshot,
)
from kairospy.domain.reference import MarketRef
from .state import AccountState


@dataclass(frozen=True, slots=True)
class EquityCurvePoint:
    time: datetime
    equity: Decimal
    selected_balance: Decimal
    positions: tuple[tuple[str, Decimal], ...]


class AccountViewSource(Protocol):
    def get(self, key: str, default: object = None) -> object:
        ...

    def require(self, key: str) -> object:
        ...

    def envelopes(self) -> Mapping[str, object]:
        ...


class AccountViewKeys:
    segments = "account.segments"
    capabilities = "account.capabilities"
    fees = "account.fees"
    market_profiles = "account.market_profiles"
    detail_prefix = "account.detail"
    portfolio_prefix = "account.portfolio"
    current_prefix = "account.current"

    @staticmethod
    def current(context: AccountRuntimeContext) -> str:
        parts = [
            "account",
            "current",
            context.environment.value,
            context.segment.broker,
            context.segment.account_id,
        ]
        if context.segment.key:
            parts.extend(context.segment.key.split(":"))
        return ".".join(_key_part(part) for part in parts)

    @staticmethod
    def detail(context: AccountRuntimeContext) -> str:
        parts = [
            "account",
            "detail",
            context.environment.value,
            context.segment.broker,
            context.segment.account_id,
        ]
        if context.segment.key:
            parts.extend(context.segment.key.split(":"))
        return ".".join(_key_part(part) for part in parts)

    @staticmethod
    def portfolio(context: AccountRuntimeContext) -> str:
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
class AccountSegmentSummary:
    key: str
    alias: str
    account_alias: str
    account_index: int
    account_key: str
    segment_key: str
    environment: str
    broker: str
    account_id: str
    segment_model: str
    segment_qualifier: str = ""


@dataclass(frozen=True, slots=True)
class AccountSegmentsView:
    total_count: int = 0
    segments: tuple[AccountSegmentSummary, ...] = ()


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
    profiles: tuple[AccountMarketProfile, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountCurrentView:
    context: AccountRuntimeContext
    identity: ExternalAccountIdentity | None = None
    segment: AccountSegment | None = None
    segment_model: str = ""
    segment_qualifier: str = ""
    event_count: int = 0
    last_event_time: datetime | None = None
    source: AccountSource | str | None = None
    balances: tuple[AccountBalance, ...] = ()
    margins: tuple[MarginState, ...] = ()
    collaterals: tuple[CollateralBalance, ...] = ()
    liabilities: tuple[LiabilitySnapshot, ...] = ()
    positions: tuple[PositionSnapshot, ...] = ()
    open_orders: tuple[OpenOrderSnapshot, ...] = ()
    pending_orders: tuple[OrderState, ...] = ()
    stale: bool = False
    selected_balance: Decimal | None = None
    equity: Decimal | None = None
    initial_equity: Decimal | None = None
    net_profit: Decimal | None = None
    total_return: Decimal | None = None
    valuation_asset: AssetCode | None = None

    def __post_init__(self) -> None:
        if self.valuation_asset is not None and not isinstance(self.valuation_asset, AssetCode):
            object.__setattr__(self, "valuation_asset", AssetCode(self.valuation_asset))


@dataclass(frozen=True, slots=True)
class AccountDetailView:
    context: AccountRuntimeContext
    identity: ExternalAccountIdentity | None = None
    segment: AccountSegment | None = None
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
    segments: tuple[AccountCurrentView, ...] = ()
    balances: tuple[AccountBalance, ...] = ()
    margins: tuple[MarginState, ...] = ()
    collaterals: tuple[CollateralBalance, ...] = ()
    liabilities: tuple[LiabilitySnapshot, ...] = ()
    positions: tuple[PositionSnapshot, ...] = ()
    open_orders: tuple[OpenOrderSnapshot, ...] = ()
    selected_balance: Decimal | None = None
    equity: Decimal | None = None
    stale: bool = False
    updated_at: datetime | None = None
    valuation_asset: AssetCode | None = None
    aggregate_complete: bool = False

    def __post_init__(self) -> None:
        if self.valuation_asset is not None and not isinstance(self.valuation_asset, AssetCode):
            object.__setattr__(self, "valuation_asset", AssetCode(self.valuation_asset))


@dataclass(frozen=True, slots=True)
class EquityCurveView:
    account: AccountRuntimeContext
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

    def account(self, key: str | int) -> "ExternalAccountReader":
        return ExternalAccountReader(self.source, key)

    def has_account(self, key: str | int) -> bool:
        return any(_account_key_text(key) in _account_match_keys(item) for item in _account_segments(self.source))

    def current(self, key: str | None = None) -> AccountCurrentView:
        if key is not None:
            return cast(AccountCurrentView, self.source.require(_account_view_key(self.source, key)))
        account_keys = _account_current_keys(self.source)
        if not account_keys:
            raise KeyError("no account view is available")
        if len(account_keys) > 1:
            raise ValueError("multiple account views are available; pass an account key")
        return cast(AccountCurrentView, self.source.require(account_keys[0]))

    def detail(self, key: str | None = None) -> AccountDetailView:
        current_key = _account_view_key(self.source, key) if key is not None else _single_account_current_key(self.source)
        return cast(AccountDetailView, self.source.require(_detail_key_from_current_key(current_key)))

    def segment(self, key: str) -> "ExternalAccountReader":
        return self.account(key)

    def balance(self, currency: AssetCode | str, *, account: str | None = None) -> AccountBalance | None:
        balances = tuple(getattr(self.current(account), "balances", ()) or ())
        return next((item for item in balances if item.currency == currency), None)

    def position(self, instrument: str, *, account: str | None = None) -> PositionSnapshot | None:
        instrument_id = str(instrument)
        positions = tuple(getattr(self.current(account), "positions", ()) or ())
        return next((item for item in positions if str(item.instrument_id) == instrument_id), None)

    def fees(self, *, account: str | None = None) -> tuple[AccountFeeSchedule, ...]:
        view = self.source.get(AccountViewKeys.fees, None)
        schedules = tuple(getattr(view, "fees", ()) or ())
        if account is None:
            return schedules
        current = self.current(account)
        segment = getattr(current, "segment", None)
        return tuple(item for item in schedules if item.segment == segment)

    def market_profile(self, account: AccountSegment, market: MarketRef) -> AccountMarketProfile | None:
        view = self.source.get(AccountViewKeys.market_profiles, None)
        profiles = tuple(getattr(view, "profiles", ()) or ())
        market_id = str(getattr(market, "market_id", market))
        return next(
            (
                item for item in profiles
                if getattr(getattr(item, "account", None), "segment", None) == account
                and str(getattr(getattr(item, "market", None), "market_id", "")) == market_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class ExternalAccountReader:
    source: AccountViewSource
    account_key: str | int

    def current(self) -> AccountCurrentView:
        return cast(AccountCurrentView, self.source.require(_account_view_key_for_account(self.source, self.account_key, None)))

    def detail(self, segment: str | None = None) -> AccountDetailView:
        current_key = _account_view_key_for_account(self.source, self.account_key, segment)
        return cast(AccountDetailView, self.source.require(_detail_key_from_current_key(current_key)))

    def segment(self, key: str | None = None) -> "AccountSegmentReader":
        return AccountSegmentReader(self.source, self.account_key, key)

    def overview(self) -> AccountPortfolioView:
        return cast(AccountPortfolioView, self.source.require(_portfolio_view_key_for_account(self.source, self.account_key)))

    def balance(self, currency: AssetCode | str, *, segment: str | None = None) -> AccountBalance | None:
        balances = tuple(getattr(self.source.require(_account_view_key_for_account(self.source, self.account_key, segment)), "balances", ()) or ())
        return next((item for item in balances if item.currency == currency), None)

    def position(self, instrument: str, *, segment: str | None = None) -> PositionSnapshot | None:
        instrument_id = str(instrument)
        positions = tuple(getattr(self.source.require(_account_view_key_for_account(self.source, self.account_key, segment)), "positions", ()) or ())
        return next((item for item in positions if str(item.instrument_id) == instrument_id), None)

    def fees(self, *, segment: str | None = None) -> tuple[AccountFeeSchedule, ...]:
        schedules = tuple(getattr(self.source.get(AccountViewKeys.fees, None), "fees", ()) or ())
        if segment is None:
            account_text = _account_key_text(self.account_key)
            account_segments = tuple(item for item in _account_segments(self.source) if account_text in _account_match_keys(item))
            refs = {getattr(self.source.require(str(getattr(item, "key", ""))), "segment", None) for item in account_segments}
            return tuple(item for item in schedules if item.segment in refs)
        account_view = self.source.require(_account_view_key_for_account(self.source, self.account_key, segment))
        selected = getattr(account_view, "segment", None)
        return tuple(item for item in schedules if item.segment == selected)


@dataclass(frozen=True, slots=True)
class AccountSegmentReader:
    source: AccountViewSource
    account_key: str | int
    segment_key: str | None = None

    def current(self) -> AccountCurrentView:
        return cast(AccountCurrentView, self.source.require(_account_view_key_for_account(self.source, self.account_key, self.segment_key)))

    @property
    def detail(self) -> AccountDetailView:
        return cast(
            AccountDetailView,
            self.source.require(_detail_key_from_current_key(_account_view_key_for_account(self.source, self.account_key, self.segment_key))),
        )

    @property
    def market(self) -> "AccountMarketCollectionReader":
        return AccountMarketCollectionReader(self.source, self.account_key, self.segment_key)

    def fees(self) -> tuple[AccountFeeSchedule, ...]:
        current = self.current()
        selected = getattr(current, "segment", None)
        view = self.source.get(AccountViewKeys.fees, None)
        return tuple(item for item in (getattr(view, "fees", ()) or ()) if item.segment == selected)


@dataclass(frozen=True, slots=True)
class AccountMarketSegmentReader:
    source: AccountViewSource
    account_key: str | int
    segment_key: str | None
    market_ref: MarketRef

    @property
    def profile(self) -> AccountMarketProfile | None:
        current = self.source.require(_account_view_key_for_account(self.source, self.account_key, self.segment_key))
        segment = getattr(current, "segment", None)
        return AccountViewReader(self.source).market_profile(segment, self.market_ref)

    @property
    def fee(self) -> AccountFeeSchedule | None:
        profile = self.profile
        return None if profile is None else getattr(profile, "fee", None)

    @property
    def detail(self) -> AccountMarketProfile:
        profile = self.profile
        if profile is None:
            raise KeyError(f"no account market profile is available for {self.market_ref}")
        return profile


@dataclass(frozen=True, slots=True)
class AccountMarketCollectionReader:
    source: AccountViewSource
    account_key: str | int
    segment_key: str | None

    def __call__(self, market: MarketRef) -> AccountMarketSegmentReader:
        return AccountMarketSegmentReader(self.source, self.account_key, self.segment_key, market)

    def get(self, market: MarketRef) -> AccountMarketSegmentReader:
        return self(market)


ACCOUNT_SCOPES_SCHEMA = ViewSchema(
    AccountViewKeys.segments,
    "account",
    fields=(
        ViewFieldSchema("total_count", "known account segment count", "runtime state", "account port"),
        ViewFieldSchema("segments", "account segment summaries", "runtime state", "account port"),
    ),
    mutability="runtime_writable",
    evidence="runtime account segment index",
)

ACCOUNT_CAPABILITIES_SCHEMA = ViewSchema(
    AccountViewKeys.capabilities,
    "account",
    fields=(
        ViewFieldSchema("total_count", "known account capability count", "runtime state", "account port"),
        ViewFieldSchema("capabilities", "account segment capabilities", "runtime state", "account port"),
    ),
    mutability="runtime_writable",
    evidence="runtime account capability index",
)


ACCOUNT_FEES_SCHEMA = ViewSchema(
    AccountViewKeys.fees,
    "account",
    fields=(
        ViewFieldSchema("total_count", "known account fee schedule count", "runtime state", "account port"),
        ViewFieldSchema("fees", "account segment maker/taker fee schedules", "runtime state", "account port"),
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
            ViewFieldSchema("segment", "account segment identity", "runtime account event", "account port"),
            ViewFieldSchema("segment_model", "account segment or wallet kind", "runtime account event", "account port"),
            ViewFieldSchema("segment_qualifier", "account segment qualifier", "runtime account event", "account port"),
            ViewFieldSchema("event_count", "consumed account event count", "runtime sequence", "account view state"),
            ViewFieldSchema("last_event_time", "latest account event time", "event time", "account event"),
            ViewFieldSchema("source", "account data source", "event time", "account state or snapshot"),
            ViewFieldSchema("balances", "account balances", "event time", "account state or snapshot"),
            ViewFieldSchema("margins", "account margin states", "event time", "account state or snapshot"),
            ViewFieldSchema("collaterals", "multi-asset collateral balances", "event time", "account state or snapshot"),
            ViewFieldSchema("liabilities", "account liabilities and borrow states", "event time", "account state or snapshot"),
            ViewFieldSchema("positions", "account positions", "event time", "account state or snapshot"),
            ViewFieldSchema("open_orders", "venue open orders", "event time", "account state or snapshot"),
            ViewFieldSchema("pending_orders", "local active order states", "runtime state", "account event"),
            ViewFieldSchema("stale", "account state staleness flag", "event time", "account state"),
            ViewFieldSchema("selected_balance", "balance of the selected valuation asset", "event time", "account balances"),
            ViewFieldSchema("equity", "marked account equity", "event time", "account event or balances"),
            ViewFieldSchema("initial_equity", "first or configured account equity baseline", "launch baseline", "account view state"),
            ViewFieldSchema("net_profit", "equity minus baseline", "event time", "account view state"),
            ViewFieldSchema("total_return", "net profit divided by baseline", "event time", "account view state"),
            ViewFieldSchema("valuation_asset", "asset used for selected balance and equity values", "account configuration", "account view state"),
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
            ViewFieldSchema("segment", "account segment identity", "runtime account event", "account port"),
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
            ViewFieldSchema("segments", "included account segment views", "runtime state", "account current views"),
            ViewFieldSchema("balances", "aggregated account balances", "runtime state", "account current views"),
            ViewFieldSchema("collaterals", "aggregated multi-asset collateral balances", "runtime state", "account current views"),
            ViewFieldSchema("positions", "aggregated account positions", "runtime state", "account current views"),
            ViewFieldSchema("selected_balance", "aggregated selected-asset balance when the asset is unambiguous", "runtime state", "account current views"),
            ViewFieldSchema("equity", "aggregated equity when currency is unambiguous", "runtime state", "account current views"),
            ViewFieldSchema("stale", "whether any included segment is stale", "runtime state", "account current views"),
            ViewFieldSchema("valuation_asset", "asset used for aggregate values", "valuation configuration", "account current views"),
            ViewFieldSchema("aggregate_complete", "whether aggregate values are complete and comparable", "runtime state", "account current views"),
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
    segments = _account_segments(source)
    for item in segments:
        item_key = str(getattr(item, "key", ""))
        if key in {
            item_key,
            str(getattr(item, "alias", "")),
            _segment_identity_key(item),
        }:
            return item_key
    scope_matches = tuple(
        str(getattr(item, "key", ""))
        for item in segments
        if key in {str(getattr(item, "segment_model", "")), str(getattr(item, "segment_qualifier", ""))}
    )
    if len(scope_matches) == 1:
        return scope_matches[0]
    if len(scope_matches) > 1:
        raise ValueError(f"multiple account segments match account key: {key}; use an account alias or full account segment key")
    matches = tuple(view_key for view_key in _account_current_keys(source) if view_key.endswith(f".{key}"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"multiple account views match account key: {key}")
    return key


def _account_view_key_for_account(source: AccountViewSource, account_key: str | int, segment_key: str | None) -> str:
    account_text = _account_key_text(account_key)
    account_matches = tuple(item for item in _account_segments(source) if account_text in _account_match_keys(item))
    if not account_matches:
        raise KeyError(f"unknown account: {account_text}")
    if segment_key is None:
        if len(account_matches) == 1:
            return str(getattr(account_matches[0], "key", ""))
        raise ValueError(f"multiple segments are available for account: {account_text}; pass a segment key")
    segment_text = _segment_key_text(segment_key)
    scope_matches = tuple(item for item in account_matches if segment_text in _segment_match_keys(item))
    if len(scope_matches) == 1:
        return str(getattr(scope_matches[0], "key", ""))
    if len(scope_matches) > 1:
        raise ValueError(f"multiple segments match account {account_text!r} and segment {segment_text!r}")
    raise KeyError(f"unknown account segment: {account_text}.{segment_text}")


def _portfolio_view_key_for_account(source: AccountViewSource, account_key: str | int) -> str:
    account_text = _account_key_text(account_key)
    account_matches = tuple(item for item in _account_segments(source) if account_text in _account_match_keys(item))
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


def account_current_view_key(context: AccountRuntimeContext) -> str:
    return AccountViewKeys.current(context)


def account_detail_view_key(context: AccountRuntimeContext) -> str:
    return AccountViewKeys.detail(context)


def _account_segments(source: AccountViewSource) -> tuple[AccountSegmentSummary, ...]:
    scopes_view = source.get(AccountViewKeys.segments, None)
    return tuple(getattr(scopes_view, "segments", ()) or ())


def _account_match_keys(item: AccountSegmentSummary) -> set[str]:
    return {
        str(getattr(item, "account_index", "")),
        str(getattr(item, "account_alias", "")),
        str(getattr(item, "account_key", "")),
        ".".join(_key_part(part) for part in (getattr(item, "broker", ""), getattr(item, "account_id", "")) if part),
        str(getattr(item, "account_id", "")),
    }


def _segment_match_keys(item: AccountSegmentSummary) -> set[str]:
    return {
        str(getattr(item, "segment_key", "")),
        str(getattr(item, "segment_model", "")),
        str(getattr(item, "segment_qualifier", "")),
        str(getattr(item, "alias", "")),
        _segment_identity_key(item),
        str(getattr(item, "key", "")),
    }


def _account_key_text(value: str | int) -> str:
    if isinstance(value, bool):
        raise ValueError("account key must be an alias or integer index")
    return str(value)


def _segment_key_text(value: str | AccountModel) -> str:
    return value.value if isinstance(value, AccountModel) else value


def _segment_identity_key(item: AccountSegmentSummary) -> str:
    parts = [
        str(getattr(item, "broker", "")),
        str(getattr(item, "account_id", "")),
        str(getattr(item, "segment_model", "")),
        str(getattr(item, "segment_qualifier", "")),
    ]
    return ".".join(_key_part(part) for part in parts if part)


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in ("".join(character if character.isalnum() else "_" for character in text)).split("_") if part)


__all__ = [
    "ACCOUNT_SCOPES_SCHEMA",
    "ACCOUNT_CAPABILITIES_SCHEMA",
    "ACCOUNT_FEES_SCHEMA",
    "ACCOUNT_MARKET_PROFILES_SCHEMA",
    "AccountSegmentSummary",
    "AccountSegmentsView",
    "AccountCapabilitiesView",
    "AccountCurrentView",
    "AccountDetailView",
    "ACCOUNT_EQUITY_CURVE_SCHEMA",
    "EquityCurveView",
    "EquityCurvePoint",
    "AccountFeesView",
    "AccountMarketProfilesView",
    "AccountPortfolioView",
    "ExternalAccountReader",
    "AccountSegmentReader",
    "AccountMarketSegmentReader",
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
