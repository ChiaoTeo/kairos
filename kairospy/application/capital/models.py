from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum

from kairospy.domain_types import AccountId, SegmentKey


class FundingPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class FundingObjectiveStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class CapitalReadiness(StrEnum):
    DISABLED = "disabled"
    WAITING_FOR_ACCOUNTS = "waiting_for_accounts"
    WAITING_FOR_FACTS = "waiting_for_facts"
    DEGRADED = "degraded"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class FundingLocation:
    account_id: AccountId
    segment: SegmentKey
    asset: str
    broker: str = "binance"

    def __post_init__(self) -> None:
        account_id = AccountId(str(self.account_id))
        segment = SegmentKey(str(self.segment))
        asset = self.asset.strip().upper()
        if not asset:
            raise ValueError("Funding location asset is required")
        broker = self.broker.strip().lower()
        if not broker:
            raise ValueError("Funding location broker is required")
        object.__setattr__(self, "account_id", account_id)
        object.__setattr__(self, "segment", segment)
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "broker", broker)


@dataclass(frozen=True, slots=True)
class FundingObjective:
    """A Strategy liquidity goal; never a transfer or route command."""

    objective_id: str
    version: int
    destination: FundingLocation
    desired_available: Decimal
    required_by: datetime
    expires_at: datetime
    priority: FundingPriority = FundingPriority.NORMAL
    confidence: Decimal = Decimal("1")
    strategy_decision_id: str | None = None

    def __post_init__(self) -> None:
        objective_id = self.objective_id.strip()
        if not objective_id:
            raise ValueError("Funding objective id is required")
        if self.version <= 0:
            raise ValueError("Funding objective version must be positive")
        if self.desired_available < 0:
            raise ValueError("Funding objective desired_available cannot be negative")
        required_by = _utc(self.required_by, "required_by")
        expires_at = _utc(self.expires_at, "expires_at")
        if expires_at < required_by:
            raise ValueError("Funding objective cannot expire before required_by")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("Funding objective confidence must be between 0 and 1")
        decision_id = self.strategy_decision_id
        if decision_id is not None and not decision_id.strip():
            raise ValueError("strategy_decision_id cannot be blank")
        object.__setattr__(self, "objective_id", objective_id)
        object.__setattr__(self, "required_by", required_by)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class FundingObjectiveReceipt:
    objective_id: str
    version: int
    status: FundingObjectiveStatus
    message: str | None = None


@dataclass(frozen=True, slots=True)
class CapitalDemand:
    demand_id: str
    idempotency_key: str
    destination: FundingLocation
    observed_shortfall: Decimal
    observed_at: datetime
    required_by: datetime
    expires_at: datetime
    account_watermark: int
    risk_watermark: int
    destination_lease_fence: str
    priority: FundingPriority = FundingPriority.NORMAL
    confidence: Decimal = Decimal("1")
    causal_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.demand_id.strip() or not self.idempotency_key.strip():
            raise ValueError("Capital demand identity is required")
        if self.observed_shortfall <= 0:
            raise ValueError("Capital demand shortfall must be positive")
        observed_at = _utc(self.observed_at, "observed_at")
        required_by = _utc(self.required_by, "required_by")
        expires_at = _utc(self.expires_at, "expires_at")
        if not observed_at <= required_by <= expires_at:
            raise ValueError(
                "Capital demand requires observed_at <= required_by <= expires_at"
            )
        if self.account_watermark <= 0 or self.risk_watermark <= 0:
            raise ValueError("Capital demand requires Account and Risk watermarks")
        if not self.destination_lease_fence.strip():
            raise ValueError("Capital demand destination lease fence is required")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("Capital demand confidence must be between 0 and 1")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "required_by", required_by)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class CapitalDemandReceipt:
    demand_id: str
    status: FundingObjectiveStatus
    message: str | None = None


@dataclass(frozen=True, slots=True)
class CapitalFundingHorizon:
    required_by: datetime
    objective_ids: tuple[str, ...]
    demand_ids: tuple[str, ...]
    desired_available: Decimal


@dataclass(frozen=True, slots=True)
class CapitalAvailability:
    capital_group_id: str | None
    readiness: CapitalReadiness
    location: FundingLocation | None = None
    policy_minimum: Decimal | None = None
    policy_default_target: Decimal | None = None
    policy_maximum: Decimal | None = None
    policy_version: int | None = None
    active_objective_ids: tuple[str, ...] = ()
    active_demand_ids: tuple[str, ...] = ()
    funding_horizons: tuple[CapitalFundingHorizon, ...] = ()
    desired_target: Decimal | None = None
    observed_available: Decimal | None = None
    effective_target: Decimal | None = None
    deficit: Decimal | None = None
    account_watermark: int | None = None
    risk_policy_version: int | None = None
    risk_watermark: int | None = None
    reason: str | None = None


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"Funding objective {name} must be timezone-aware")
    return value.astimezone(timezone.utc)
