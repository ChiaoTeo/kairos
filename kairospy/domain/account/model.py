from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from kairospy.domain.reference import ExternalAccountId, AccountSegmentId, BrokerId, InstrumentId, MarketRef, ProductFamily


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


@dataclass(frozen=True, slots=True, eq=False)
class AssetCode:
    """Canonical symbol for an asset held or used by an account."""

    value: str

    def __post_init__(self) -> None:
        value = str(self.value).strip().upper()
        if not value:
            raise ValueError("asset code cannot be empty")
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, AssetCode):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other.strip().upper()
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        return self.value < str(other).upper()


class MarginScope(StrEnum):
    ACCOUNT = "account"
    INSTRUMENT = "instrument"
    POSITION = "position"


class MarginMode(StrEnum):
    """How derivative position collateral is shared or isolated."""

    CROSS = "cross"
    ISOLATED = "isolated"


class PositionMode(StrEnum):
    ONE_WAY = "one_way"
    HEDGE = "hedge"


@dataclass(frozen=True, slots=True)
class LeveragePolicy:
    maximum: Decimal
    default: Decimal
    adjustable: bool = True

    def __post_init__(self) -> None:
        maximum = Decimal(str(self.maximum))
        default = Decimal(str(self.default))
        if maximum <= 0 or default <= 0 or default > maximum:
            raise ValueError("leverage policy requires 0 < default <= maximum")
        object.__setattr__(self, "maximum", maximum)
        object.__setattr__(self, "default", default)


@dataclass(frozen=True, slots=True)
class AccountPolicy:
    """ExternalAccount-level permissions independent of a particular market."""

    can_trade: bool = False
    can_borrow: bool = False
    can_transfer_in: bool = True
    can_transfer_out: bool = True
    can_hold_assets: bool = True
    can_hold_position: bool = False


@dataclass(frozen=True, slots=True)
class MarginPolicy:
    """Margin constraints for a segment or risk unit."""

    modes: tuple[MarginMode, ...] = (MarginMode.CROSS,)
    initial_ratio: Decimal | None = None
    maintenance_ratio: Decimal | None = None

    def __post_init__(self) -> None:
        modes = tuple(_enum(mode, MarginMode, "margin mode") for mode in self.modes)
        if not modes:
            raise ValueError("margin policy requires at least one margin mode")
        object.__setattr__(self, "modes", modes)
        for name in ("initial_ratio", "maintenance_ratio"):
            value = getattr(self, name)
            if value is not None:
                value = Decimal(str(value))
                if value < 0 or value > 1:
                    raise ValueError(f"{name} must be between 0 and 1")
                object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class PositionPolicy:
    """Position accounting and order-reduction rules."""

    modes: tuple[PositionMode, ...] = (PositionMode.ONE_WAY,)
    allow_reduce_only: bool = True
    leverage: LeveragePolicy | None = None

    def __post_init__(self) -> None:
        modes = tuple(_enum(mode, PositionMode, "position mode") for mode in self.modes)
        if not modes:
            raise ValueError("position policy requires at least one position mode")
        object.__setattr__(self, "modes", modes)


@dataclass(frozen=True, slots=True)
class FeePolicy:
    """Fee payment and rate policy owned by the account boundary."""

    maker: Decimal | None = None
    taker: Decimal | None = None
    payment_currency: AssetCode | str | None = None

    def __post_init__(self) -> None:
        for name in ("maker", "taker"):
            value = getattr(self, name)
            if value is not None:
                value = Decimal(str(value))
                if value < 0:
                    raise ValueError(f"{name} fee cannot be negative")
                object.__setattr__(self, name, value)
        if self.payment_currency is not None:
            object.__setattr__(self, "payment_currency", _asset(self.payment_currency))


@dataclass(frozen=True, slots=True)
class SettlementPolicy:
    """Settlement currencies and whether cross-currency settlement is allowed."""

    currencies: tuple[AssetCode | str, ...] = ()
    cross_currency: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "currencies", tuple(_asset(currency) for currency in self.currencies))

    def __post_init__(self) -> None:
        currencies = tuple(_required_text(currency, "settlement currency") for currency in self.currencies)
        if len(currencies) != len(set(currencies)):
            raise ValueError("settlement policy currencies must be unique")
        object.__setattr__(self, "currencies", currencies)


@dataclass(frozen=True, slots=True)
class AccountPolicySet:
    """Explicit rule composition for one account segment/profile."""

    account: AccountPolicy = AccountPolicy()
    margin: MarginPolicy | None = None
    position: PositionPolicy | None = None
    fee: FeePolicy | None = None
    settlement: SettlementPolicy | None = None


@dataclass(frozen=True, slots=True)
class LeverageState:
    segment: "AccountSegment"
    instrument_id: InstrumentId | str
    value: Decimal
    source: AccountSource

    def __post_init__(self) -> None:
        value = Decimal(str(self.value))
        if value <= 0:
            raise ValueError("leverage must be positive")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))


class AccountModel(StrEnum):
    """How an external account organizes capital, risk and settlement."""

    NO_MARGIN = "no_margin"
    MARGIN = "margin"
    CONTRACT = "contract"
    CONTRACT_UNIFIED = "contract_unified"
    UNIFIED = "unified"
    PORTFOLIO_MARGIN = "portfolio_margin"


class AccountStatus(StrEnum):
    UNKNOWN = "unknown"
    READY = "ready"
    TYPE_MISMATCH = "type_mismatch"
    RECONCILING = "reconciling"
    SUSPENDED = "suspended"
    UNAVAILABLE = "unavailable"


class AccountTransitionStatus(StrEnum):
    REQUESTED = "requested"
    RECONCILING = "reconciling"
    COMPLETED = "completed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class AccountModelTransition:
    account: ExternalAccountIdentity
    from_model: AccountModel | None
    to_model: AccountModel
    status: AccountTransitionStatus
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AccountModelChangedEvent:
    """Fact emitted after an external account model switch is confirmed."""

    transition: AccountModelTransition
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("account model change timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True, order=True)
class ExternalAccountIdentity:
    broker: BrokerId | str
    account_id: ExternalAccountId | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "broker", _id(self.broker, BrokerId, "broker"))
        object.__setattr__(self, "account_id", _id(self.account_id, ExternalAccountId, "account_id"))

    @property
    def value(self) -> str:
        return f"{self.broker}:{self.account_id}"


@dataclass(frozen=True, slots=True, order=True, init=False)
class AccountSegment:
    """A uniquely addressable trading or settlement segment within an external account."""

    identity: ExternalAccountIdentity
    segment_id: AccountSegmentId
    model: AccountModel
    product_family: ProductFamily | None = None
    qualifier: str = ""

    def __init__(
        self,
        identity_or_broker: ExternalAccountIdentity | BrokerId | str,
        account_id_or_segment_id: ExternalAccountId | str | None = None,
        model: AccountModel = AccountModel.NO_MARGIN,
        product_family: ProductFamily | None = None,
        qualifier: str = "",
    ) -> None:
        if isinstance(identity_or_broker, ExternalAccountIdentity):
            identity = identity_or_broker
            if account_id_or_segment_id is None:
                raise ValueError("segment id is required")
        else:
            if account_id_or_segment_id is None:
                raise ValueError("account id is required")
            identity = ExternalAccountIdentity(identity_or_broker, account_id_or_segment_id)
        normalized_segment_id = _id(account_id_or_segment_id, AccountSegmentId, "segment id")
        normalized_qualifier = _required_text(qualifier, "segment qualifier") if qualifier else ""
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "segment_id", normalized_segment_id)
        object.__setattr__(self, "model", _enum(model, AccountModel, "account model"))
        object.__setattr__(self, "product_family", None if product_family is None else _enum(product_family, ProductFamily, "product family"))
        object.__setattr__(self, "qualifier", normalized_qualifier)

    @property
    def broker(self) -> BrokerId | str:
        return self.identity.broker

    @property
    def account_id(self) -> ExternalAccountId | str:
        return self.identity.account_id

    @property
    def key(self) -> str:
        parts = [str(self.segment_id), str(self.model)]
        if self.product_family is not None:
            parts.append(str(self.product_family))
        if self.qualifier:
            parts.append(self.qualifier)
        return ":".join(parts)

    @property
    def value(self) -> str:
        return f"{self.identity.value}:{self.key}"


def account_segment_from_name(identity: ExternalAccountIdentity, name: str) -> AccountSegment:
    """Build a typed account segment from its user-facing name."""
    normalized = name.strip().lower().replace("-", "_")
    if normalized == "equity":
        # Equity is an asset type, not an account product.  Keep accepting
        # the legacy name while creating the canonical spot account segment.
        normalized = "spot"
    products = {
        "spot": (AccountModel.NO_MARGIN, ProductFamily.SPOT),
        "usd_m_futures": (AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES),
        "coin_m_futures": (AccountModel.CONTRACT, ProductFamily.COIN_M_FUTURES),
        "options": (AccountModel.CONTRACT, ProductFamily.OPTIONS),
        "swap": (AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES),
    }
    models = {
        "margin": AccountModel.MARGIN,
        "cross_margin": AccountModel.MARGIN,
        "isolated_margin": AccountModel.MARGIN,
        "contract_unified": AccountModel.CONTRACT_UNIFIED,
        "unified": AccountModel.UNIFIED,
        "portfolio_margin": AccountModel.PORTFOLIO_MARGIN,
    }
    if normalized in products:
        model, product_family = products[normalized]
        return AccountSegment(identity, normalized, model=model, product_family=product_family)
    if normalized in {"funding", "earn"}:
        # Earn/funding are account services, not trading product families.
        return AccountSegment(identity, normalized, model=AccountModel.NO_MARGIN)
    if normalized in models:
        return AccountSegment(identity, normalized, model=models[normalized])
    raise ValueError(f"unknown account segment: {name}")


@dataclass(frozen=True, slots=True)
class ExternalAccount:
    """Aggregate root for one external account and all of its segments."""

    identity: ExternalAccountIdentity
    segments: tuple[AccountSegment, ...] = ()
    configured_model: AccountModel | None = None
    observed_model: AccountModel | None = None
    status: AccountStatus = AccountStatus.UNKNOWN

    def __post_init__(self) -> None:
        if any(segment.identity != self.identity for segment in self.segments):
            raise ValueError("all account segments must belong to the account identity")
        segment_ids = [segment.segment_id for segment in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("account segment ids must be unique")
        models = {segment.model for segment in self.segments}
        transitioning = self.status is AccountStatus.RECONCILING
        if not transitioning and AccountModel.UNIFIED in models and models != {AccountModel.UNIFIED}:
            raise ValueError("unified account segments cannot coexist with independent account models")
        if not transitioning and AccountModel.CONTRACT_UNIFIED in models and models != {AccountModel.CONTRACT_UNIFIED}:
            raise ValueError("contract-unified segments cannot coexist with independent account models")
        if not transitioning and self.configured_model in {AccountModel.UNIFIED, AccountModel.CONTRACT_UNIFIED} and models and models != {self.configured_model}:
            raise ValueError("configured unified account model must match every account segment")

    def segment(self, segment_id: str) -> AccountSegment:
        for segment in self.segments:
            if segment.segment_id == segment_id:
                return segment
        raise KeyError(f"unknown account segment: {segment_id}")

    def with_segment(self, segment: AccountSegment) -> "ExternalAccount":
        if segment.identity != self.identity:
            raise ValueError("account segment identity does not match account")
        if any(item.segment_id == segment.segment_id for item in self.segments):
            raise ValueError(f"account segment already exists: {segment.segment_id}")
        return ExternalAccount(self.identity, (*self.segments, segment), self.configured_model, self.observed_model, self.status)

    def request_model_switch(self, target: AccountModel, *, reason: str = "") -> tuple["ExternalAccount", AccountModelTransition]:
        target = _enum(target, AccountModel, "account model")
        if self.observed_model is target:
            transition = AccountModelTransition(self.identity, self.observed_model, target, AccountTransitionStatus.REJECTED, "account already uses target model")
            return self, transition
        transition = AccountModelTransition(self.identity, self.observed_model, target, AccountTransitionStatus.REQUESTED, reason)
        return ExternalAccount(self.identity, self.segments, target, self.observed_model, AccountStatus.RECONCILING), transition

    def observe_model(self, observed: AccountModel) -> "ExternalAccount":
        observed = _enum(observed, AccountModel, "observed account model")
        status = AccountStatus.READY if self.configured_model in (None, observed) else AccountStatus.TYPE_MISMATCH
        return ExternalAccount(self.identity, self.segments, self.configured_model, observed, status)


@dataclass(frozen=True, slots=True, init=False)
class AccountRuntimeContext:
    segment: AccountSegment
    environment: Environment

    def __init__(self, segment: AccountSegment, environment: Environment) -> None:
        if segment is None:
            raise ValueError("account segment is required")
        object.__setattr__(self, "segment", segment)
        object.__setattr__(self, "environment", environment)

    @property
    def identity(self) -> ExternalAccountIdentity:
        return self.segment.identity

    @property
    def value(self) -> str:
        return f"{self.environment}:{self.segment.value}"


@dataclass(frozen=True, slots=True)
class AccountBalance:
    currency: AssetCode | str
    total: Decimal
    free: Decimal
    locked: Decimal
    source: AccountSource

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", _asset(self.currency))
        if self.total != self.free + self.locked:
            raise ValueError("account balance must satisfy total == free + locked")
        if self.locked < 0:
            raise ValueError("locked balance cannot be negative")

    @classmethod
    def from_total_locked(
        cls,
        currency: AssetCode | str,
        total: Decimal,
        locked: Decimal,
        *,
        source: AccountSource,
    ) -> "AccountBalance":
        return cls(currency, total, total - locked, locked, source)

    @classmethod
    def from_free_locked(
        cls,
        currency: AssetCode | str,
        free: Decimal,
        locked: Decimal,
        *,
        source: AccountSource,
    ) -> "AccountBalance":
        return cls(currency, free + locked, free, locked, source)


@dataclass(frozen=True, slots=True)
class MarginState:
    currency: AssetCode | str
    initial: Decimal
    maintenance: Decimal
    source: AccountSource
    scope: MarginScope = MarginScope.ACCOUNT
    instrument_id: InstrumentId | str | None = None
    available: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", _asset(self.currency))
        if self.initial < 0 or self.maintenance < 0:
            raise ValueError("margin values cannot be negative")
        if self.available is not None and self.available < 0:
            raise ValueError("available margin cannot be negative")
        if self.scope is not MarginScope.ACCOUNT and not self.instrument_id:
            raise ValueError("instrument or position margin requires instrument_id")
        object.__setattr__(self, "instrument_id", None if self.instrument_id is None else _id(self.instrument_id, InstrumentId, "instrument_id"))


@dataclass(frozen=True, slots=True)
class CollateralBalance:
    """An asset eligible to collateralize account or unified risk."""

    asset: AssetCode | str
    wallet: Decimal
    available: Decimal
    valuation: Decimal | None = None
    haircut: Decimal = Decimal("1")
    source: AccountSource = AccountSource.VENUE

    def __post_init__(self) -> None:
        asset = _asset(self.asset)
        wallet = Decimal(str(self.wallet))
        available = Decimal(str(self.available))
        haircut = Decimal(str(self.haircut))
        if wallet < 0 or available < 0 or available > wallet:
            raise ValueError("collateral quantities must satisfy 0 <= available <= wallet")
        if haircut <= 0 or haircut > 1:
            raise ValueError("collateral haircut must be in (0, 1]")
        valuation = None if self.valuation is None else Decimal(str(self.valuation))
        if valuation is not None and valuation < 0:
            raise ValueError("collateral valuation cannot be negative")
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "wallet", wallet)
        object.__setattr__(self, "available", available)
        object.__setattr__(self, "valuation", valuation)
        object.__setattr__(self, "haircut", haircut)


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    instrument_id: InstrumentId | str
    quantity: Decimal
    source: AccountSource
    average_price: Decimal | None = None
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    liquidation_price: Decimal | None = None
    margin_currency: AssetCode | str | None = None
    margin_mode: MarginMode | None = None
    leverage: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        if self.margin_currency is not None:
            object.__setattr__(self, "margin_currency", _asset(self.margin_currency))
        if self.quantity == 0:
            raise ValueError("zero positions should be omitted")
        if self.leverage is not None:
            leverage = Decimal(str(self.leverage))
            if leverage <= 0:
                raise ValueError("position leverage must be positive")
            object.__setattr__(self, "leverage", leverage)


@dataclass(frozen=True, slots=True)
class LiabilitySnapshot:
    currency: AssetCode | str
    principal: Decimal
    source: AccountSource
    interest: Decimal = Decimal("0")
    instrument_id: InstrumentId | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", _asset(self.currency))
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
    reserved_currency: AssetCode | str | None = None
    reserved_amount: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        if self.reserved_currency is not None:
            object.__setattr__(self, "reserved_currency", _asset(self.reserved_currency))
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
    context: AccountRuntimeContext
    balances: tuple[AccountBalance, ...]
    margins: tuple[MarginState, ...] = ()
    collaterals: tuple[CollateralBalance, ...] = ()
    liabilities: tuple[LiabilitySnapshot, ...] = ()
    positions: tuple[PositionSnapshot, ...] = ()
    open_orders: tuple[OpenOrderSnapshot, ...] = ()
    observed_at: datetime | None = None
    source: AccountSource = AccountSource.VENUE
    leverage: tuple[LeverageState, ...] = ()

    def __post_init__(self) -> None:
        currencies = [balance.currency for balance in self.balances]
        if len(currencies) != len(set(currencies)):
            raise ValueError("account snapshot cannot contain duplicate balance currencies")
        if self.observed_at is not None and self.observed_at.tzinfo is None:
            raise ValueError("account snapshot timestamp must be timezone-aware")
        if any(item.segment != self.context.segment for item in self.leverage):
            raise ValueError("account snapshot leverage segment does not match context segment")


@dataclass(frozen=True, slots=True)
class AccountCapability:
    segment: AccountSegment
    can_trade: bool = False
    can_hold_assets: bool = True
    can_hold_position: bool = False
    can_borrow: bool = False
    can_transfer_in: bool = True
    can_transfer_out: bool = True
    supported_order_types: tuple[str, ...] = ()
    settlement_assets: tuple[AssetCode | str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "settlement_assets", tuple(_asset(asset) for asset in self.settlement_assets))


@dataclass(frozen=True, slots=True)
class FeeDiscountRule:
    """Discount applied when the account pays fees with a selected asset."""

    asset: AssetCode | str
    rate: Decimal
    enabled: bool = False
    source: str = "venue"

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset", _asset(self.asset))
        object.__setattr__(self, "rate", Decimal(str(self.rate)))
        if self.rate < 0 or self.rate > 1:
            raise ValueError("fee discount rate must be between 0 and 1")
        object.__setattr__(self, "source", _required_text(self.source, "fee discount source"))


@dataclass(frozen=True, slots=True)
class FeePaymentRule:
    """Venue rule for selecting the fee currency and optional discount."""

    currency: AssetCode | str | None = None
    currency_mode: str = "venue_default"
    discount: FeeDiscountRule | None = None

    def __post_init__(self) -> None:
        if self.currency is not None:
            object.__setattr__(self, "currency", _asset(self.currency))
        object.__setattr__(self, "currency_mode", _required_text(self.currency_mode, "fee currency mode"))


@dataclass(frozen=True, slots=True)
class AccountFeeRule:
    """ExternalAccount-owned fee tier/rates before product rules are applied."""

    segment: AccountSegment
    maker: Decimal | None = None
    taker: Decimal | None = None
    tier: str | None = None
    source: str = "venue"
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("maker", "taker"):
            value = getattr(self, name)
            if value is not None:
                value = Decimal(str(value))
                object.__setattr__(self, name, value)
        if self.tier is not None:
            object.__setattr__(self, "tier", _required_text(self.tier, "account fee tier"))
        object.__setattr__(self, "source", _required_text(self.source, "account fee source"))
        if self.updated_at is not None and self.updated_at.tzinfo is None:
            raise ValueError("account fee timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class MarketFeeRule:
    """Product/market-owned fee rates before account discounts are applied."""

    market: MarketRef
    maker: Decimal
    taker: Decimal
    currency: AssetCode | str | None = None
    source: str = "venue"
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("maker", "taker"):
            value = Decimal(str(getattr(self, name)))
            object.__setattr__(self, name, value)
        if self.currency is not None:
            object.__setattr__(self, "currency", _asset(self.currency))
        object.__setattr__(self, "source", _required_text(self.source, "market fee source"))
        if self.updated_at is not None and self.updated_at.tzinfo is None:
            raise ValueError("market fee timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AccountFeeSchedule:
    segment: AccountSegment
    maker: Decimal
    taker: Decimal
    source: str = "configured"
    updated_at: datetime | None = None
    market: MarketRef | None = None
    currency: AssetCode | str | None = None
    tier: str | None = None
    account_rule: AccountFeeRule | None = None
    market_rule: MarketFeeRule | None = None
    payment: FeePaymentRule | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "maker", Decimal(str(self.maker)))
        object.__setattr__(self, "taker", Decimal(str(self.taker)))
        if self.currency is not None:
            object.__setattr__(self, "currency", _asset(self.currency))
        if self.tier is not None:
            object.__setattr__(self, "tier", _required_text(self.tier, "account fee tier"))
        object.__setattr__(self, "source", _required_text(self.source, "account fee source"))
        if self.updated_at is not None and self.updated_at.tzinfo is None:
            raise ValueError("account fee timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AccountFeeResolution:
    """Effective fee plus both source rules and payment discount semantics."""

    schedule: AccountFeeSchedule
    account_rule: AccountFeeRule | None = None
    market_rule: MarketFeeRule | None = None
    payment: FeePaymentRule | None = None
    combination: str = "exchange_defined"

    @property
    def maker(self) -> Decimal:
        return self.schedule.maker

    @property
    def taker(self) -> Decimal:
        return self.schedule.taker

    @property
    def currency(self) -> AssetCode | None:
        return self.schedule.currency

    @property
    def discount_rate(self) -> Decimal:
        discount = None if self.payment is None else self.payment.discount
        return Decimal("0") if discount is None or not discount.enabled else discount.rate

    @property
    def tier(self) -> str | None:
        return self.schedule.tier


@dataclass(frozen=True, slots=True)
class AccountMarketProfile:
    """Venue-derived account capabilities for one tradable market."""

    account: AccountRuntimeContext
    market: MarketRef
    fee: AccountFeeResolution | AccountFeeSchedule | None = None
    account_type: AccountModel | None = None
    margin_mode: MarginMode | None = None
    leverage: Decimal | None = None
    position_mode: str | None = None
    settlement_currency: AssetCode | str | None = None
    source: str = "unknown"
    observed_at: datetime | None = None
    stale: bool = False
    policies: AccountPolicySet | None = None

    def __post_init__(self) -> None:
        if self.fee is not None:
            fee_schedule = self.fee.schedule if isinstance(self.fee, AccountFeeResolution) else self.fee
            if fee_schedule.segment != self.account.segment:
                raise ValueError("account market fee segment does not match profile account")
            if fee_schedule.market is not None and fee_schedule.market != self.market:
                raise ValueError("account market fee market does not match profile market")
        if self.leverage is not None:
            object.__setattr__(self, "leverage", Decimal(str(self.leverage)))
            if self.leverage <= 0:
                raise ValueError("account market leverage must be positive")
        if self.account_type is not None:
            object.__setattr__(self, "account_type", _enum(self.account_type, AccountModel, "account model"))
        if self.margin_mode is not None:
            object.__setattr__(self, "margin_mode", _enum(self.margin_mode, MarginMode, "margin mode"))
        if self.settlement_currency is not None:
            object.__setattr__(self, "settlement_currency", _asset(self.settlement_currency))
        for name in ("position_mode",):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required_text(value, f"account market {name}"))
        object.__setattr__(self, "source", _required_text(self.source, "account market profile source"))
        if self.observed_at is not None and self.observed_at.tzinfo is None:
            raise ValueError("account market profile timestamp must be timezone-aware")


def _required_text(value: object, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} cannot be empty")
    return text


def _asset(value: AssetCode | str) -> AssetCode:
    return value if isinstance(value, AssetCode) else AssetCode(value)


def _id(value, id_type, label: str):
    if isinstance(value, id_type):
        return value
    return id_type(_required_text(value, label))


def _enum(value: object, enum_type, label: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid {label}: {value}") from exc


__all__ = [
    "AccountBalance",
    "AssetCode",
    "ExternalAccount",
    "AccountModelTransition",
    "AccountModelChangedEvent",
    "AccountTransitionStatus",
    "AccountCapability",
    "AccountRuntimeContext",
    "AccountFeeSchedule",
    "AccountFeeResolution",
    "AccountFeeRule",
    "ExternalAccountIdentity",
    "AccountMarketProfile",
    "AccountSnapshot",
    "AccountSource",
    "AccountStatus",
    "Environment",
    "FeeDiscountRule",
    "FeePaymentRule",
    "LiabilitySnapshot",
    "MarginScope",
    "PositionMode",
    "LeveragePolicy",
    "LeverageState",
    "MarketFeeRule",
    "MarginState",
    "OpenOrderSnapshot",
    "PositionSnapshot",
    "ProductFamily",
]
