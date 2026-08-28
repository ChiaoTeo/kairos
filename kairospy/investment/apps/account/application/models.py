from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.primitives.account import AccountId, BrokerId, SegmentKey
from kairospy.primitives.capital import EarnProductId
from kairospy.primitives.decimal import (
    Money,
    MoneyLike,
    Price,
    PriceLike,
    Quantity,
    QuantityLike,
    SignedQuantity,
    SignedQuantityLike,
)
from kairospy.primitives.execution import OrderId
from kairospy.primitives.integration import RemoteOrderId
from kairospy.primitives.reference import AssetId, InstrumentId, MarketId
from kairospy.primitives.time import Generation, Sequence, UnixNanos

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


class SegmentSyncMode(StrEnum):
    UNKNOWN = "unknown"
    SNAPSHOT_THEN_STREAM = "snapshot_then_stream"
    SNAPSHOT_ONLY = "snapshot_only"


class SegmentSyncLifecycle(StrEnum):
    CONFIGURED = "configured"
    BOOTSTRAPPING = "bootstrapping"
    LIVE = "live"
    SNAPSHOT_CURRENT = "snapshot_current"
    DEGRADED = "degraded"
    RESYNCING = "resyncing"
    UNAVAILABLE = "unavailable"
    STOPPED = "stopped"


class SegmentCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class PositionSide(StrEnum):
    NET = "net"
    LONG = "long"
    SHORT = "short"


class EarnHoldingState(StrEnum):
    ACTIVE = "active"
    REDEEMING = "redeeming"
    REDEEMED = "redeemed"
    UNKNOWN = "unknown"


class EarnLiquidity(StrEnum):
    IMMEDIATE = "immediate"
    NOTICE = "notice"
    FIXED_TERM = "fixed_term"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Balance:
    account_id: AccountId
    segment_key: SegmentKey
    asset: AssetId
    total: QuantityLike
    available: QuantityLike
    reserved: QuantityLike

    def __post_init__(self) -> None:
        object.__setattr__(self, "total", _quantity(self.total))
        object.__setattr__(self, "available", _quantity(self.available))
        object.__setattr__(self, "reserved", _quantity(self.reserved))


@dataclass(frozen=True, slots=True)
class Position:
    account_id: AccountId
    segment_key: SegmentKey
    instrument: InstrumentRef
    quantity: SignedQuantityLike
    position_side: PositionSide = PositionSide.NET
    average_price: PriceLike | None = None
    market_value: MoneyLike | None = None
    unrealized_pnl: MoneyLike | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", _signed_quantity(self.quantity))
        object.__setattr__(self, "average_price", _optional_price(self.average_price))
        object.__setattr__(self, "market_value", _optional_money(self.market_value))
        object.__setattr__(self, "unrealized_pnl", _optional_money(self.unrealized_pnl))


@dataclass(frozen=True, slots=True)
class EarnHolding:
    """Account-owned non-trading yield position."""

    account_id: AccountId
    segment_key: SegmentKey
    holding_key: str
    product_id: EarnProductId
    asset: AssetId
    principal: QuantityLike
    redeemable: QuantityLike | None
    state: EarnHoldingState
    liquidity: EarnLiquidity
    participant_position_id: str | None = None
    participant_state: str | None = None
    notice_seconds: int | None = None
    matures_at_unix_nanos: UnixNanos | None = None
    observed_at_unix_nanos: UnixNanos | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "principal", _quantity(self.principal))
        object.__setattr__(
            self,
            "redeemable",
            None if self.redeemable is None else _quantity(self.redeemable),
        )


@dataclass(frozen=True, slots=True)
class ObservedOrder:
    account_id: AccountId
    segment_key: SegmentKey
    order_id: OrderId
    remote_order_id: RemoteOrderId | None
    instrument: InstrumentRef
    market_id: MarketId
    quantity: QuantityLike
    filled_quantity: QuantityLike
    status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", _quantity(self.quantity))
        object.__setattr__(self, "filled_quantity", _quantity(self.filled_quantity))


@dataclass(frozen=True, slots=True)
class EquityChange:
    account_id: AccountId
    segment_key: SegmentKey
    equity: MoneyLike | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "equity", _optional_money(self.equity))


@dataclass(frozen=True, slots=True)
class AccountStatusChange:
    account_id: AccountId
    segment_key: SegmentKey
    freshness: DataFreshness
    trading_enabled: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AccountSegmentSnapshot:
    """Immutable facts for one Account segment at one current-view generation."""

    account_id: AccountId
    segment_key: SegmentKey
    broker: BrokerId
    environment: str
    account_model: str | None
    equity: MoneyLike | None
    balances: tuple[Balance, ...]
    positions: tuple[Position, ...]
    freshness: DataFreshness
    generation: Generation
    earn_holdings: tuple[EarnHolding, ...] = ()
    earn_watermark_unix_nanos: UnixNanos | None = None
    sync_mode: SegmentSyncMode = SegmentSyncMode.UNKNOWN
    sync_lifecycle: SegmentSyncLifecycle = SegmentSyncLifecycle.CONFIGURED
    completeness: SegmentCompleteness = SegmentCompleteness.UNKNOWN
    snapshot_watermark: Sequence | None = None
    event_watermark: Sequence | None = None
    channel_epoch: int | None = None
    last_event_at_unix_nanos: UnixNanos | None = None
    last_success_at_unix_nanos: UnixNanos | None = None
    last_error: str | None = None
    recovery_buffer_depth: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "equity", _optional_money(self.equity))

    @property
    def is_fresh(self) -> bool:
        return self.freshness is DataFreshness.FRESH

    def balance(self, asset: AssetId | str) -> Balance | None:
        asset_id = asset if isinstance(asset, AssetId) else AssetId(asset)
        return next((value for value in self.balances if value.asset == asset_id), None)

    def require_balance(self, asset: AssetId | str) -> Balance:
        asset_id = asset if isinstance(asset, AssetId) else AssetId(asset)
        value = self.balance(asset_id)
        if value is None:
            raise BalanceNotFoundError(self.account_id, self.segment_key, asset_id)
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
    generation: Generation
    event_sequence: Sequence = Sequence(0)

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
    """One read from every Account indexed view enabled for this Strategy launch."""

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


def _quantity(value: object) -> QuantityLike:
    if isinstance(value, QuantityLike):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return Quantity(value)
    raise TypeError("Quantity value is invalid")


def _signed_quantity(value: object) -> SignedQuantityLike:
    if isinstance(value, SignedQuantityLike):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return SignedQuantity(value)
    raise TypeError("SignedQuantity value is invalid")


def _optional_price(value: object | None) -> PriceLike | None:
    if value is None:
        return None
    if isinstance(value, PriceLike):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return Price(value)
    raise TypeError("Price value is invalid")


def _optional_money(value: object | None) -> MoneyLike | None:
    if value is None:
        return None
    if isinstance(value, MoneyLike):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return Money(value)
    raise TypeError("Money value is invalid")
