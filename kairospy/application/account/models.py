from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import AccountId, InstrumentId, SegmentKey

from .errors import (
    AccountNotEnabledError,
    AccountSegmentNotFoundError,
    BalanceNotFoundError,
    PositionNotFoundError,
)


# Common Account-owned segment identities. Public selectors still accept an
# arbitrary SegmentKey or string so provider additions do not require an SDK
# enum release.
SPOT = SegmentKey("spot")
CROSS_MARGIN = SegmentKey("cross_margin")
ISOLATED_MARGIN = SegmentKey("isolated_margin")
USD_M_FUTURES = SegmentKey("usd_m_futures")
COIN_M_FUTURES = SegmentKey("coin_m_futures")
FUNDING = SegmentKey("funding")
OPTIONS = SegmentKey("options")
EQUITY = SegmentKey("equity")


class DataFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    RESYNCING = "resyncing"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Balance:
    account_id: AccountId
    segment_key: SegmentKey
    asset: str
    total: Decimal
    available: Decimal
    reserved: Decimal


@dataclass(frozen=True, slots=True)
class Position:
    account_id: AccountId
    segment_key: SegmentKey
    instrument: InstrumentRef
    quantity: Decimal
    average_price: Decimal | None = None
    market_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ObservedOrder:
    account_id: AccountId
    segment_key: SegmentKey
    order_id: str
    remote_order_id: str | None
    instrument: InstrumentRef
    market_id: str
    quantity: Decimal
    filled_quantity: Decimal
    status: str


@dataclass(frozen=True, slots=True)
class EquityChange:
    account_id: AccountId
    segment_key: SegmentKey
    equity: Decimal | None


@dataclass(frozen=True, slots=True)
class AccountStatusChange:
    account_id: AccountId
    segment_key: SegmentKey
    freshness: DataFreshness
    trading_enabled: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AccountSegmentSnapshot:
    """Immutable facts for one Account segment at one projection generation."""

    account_id: AccountId
    segment_key: SegmentKey
    broker: str
    environment: str
    account_model: str | None
    equity: Decimal | None
    balances: tuple[Balance, ...]
    positions: tuple[Position, ...]
    freshness: DataFreshness
    generation: int

    @property
    def is_fresh(self) -> bool:
        return self.freshness is DataFreshness.FRESH

    def balance(self, asset: str) -> Balance | None:
        return next((value for value in self.balances if value.asset == asset), None)

    def require_balance(self, asset: str) -> Balance:
        value = self.balance(asset)
        if value is None:
            raise BalanceNotFoundError(self.account_id, self.segment_key, asset)
        return value

    def position(self, instrument: InstrumentRef | InstrumentId) -> Position | None:
        instrument_id = (
            instrument.id if isinstance(instrument, InstrumentRef) else instrument
        )
        return next(
            (value for value in self.positions if value.instrument.id == instrument_id),
            None,
        )

    def require_position(self, instrument: InstrumentRef | InstrumentId) -> Position:
        instrument_id = (
            instrument.id if isinstance(instrument, InstrumentRef) else instrument
        )
        value = self.position(instrument_id)
        if value is None:
            raise PositionNotFoundError(
                self.account_id, self.segment_key, instrument_id
            )
        return value


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Immutable aggregate of every enabled segment for one logical Account."""

    account_id: AccountId
    segments: tuple[AccountSegmentSnapshot, ...]
    generation: int

    def find_segment(self, segment: SegmentKey | str) -> AccountSegmentSnapshot | None:
        segment_key = _segment_key(segment)
        return next(
            (value for value in self.segments if value.segment_key == segment_key), None
        )

    def segment(self, segment: SegmentKey | str) -> AccountSegmentSnapshot:
        segment_key = _segment_key(segment)
        value = self.find_segment(segment_key)
        if value is None:
            raise AccountSegmentNotFoundError(self.account_id, segment_key)
        return value


@dataclass(frozen=True, slots=True)
class AccountsSnapshot:
    """One read from every Account mmap enabled for this Strategy launch."""

    accounts: tuple[AccountSnapshot, ...]

    def find_account(self, account: AccountId | str) -> AccountSnapshot | None:
        account_id = _account_id(account)
        return next(
            (value for value in self.accounts if value.account_id == account_id), None
        )

    def account(self, account: AccountId | str) -> AccountSnapshot:
        account_id = _account_id(account)
        value = self.find_account(account_id)
        if value is None:
            raise AccountNotEnabledError(account_id)
        return value


def _account_id(value: AccountId | str) -> AccountId:
    return value if isinstance(value, AccountId) else AccountId(value)


def _segment_key(value: SegmentKey | str) -> SegmentKey:
    return value if isinstance(value, SegmentKey) else SegmentKey(value)
