from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.decimal import Quantity, QuantityLike, Rate


class FundingPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class FundingForecastSource(StrEnum):
    STRATEGY_SCHEDULE = "strategy_schedule"
    MARKET_SESSION = "market_session"
    HISTORICAL_PEAK = "historical_peak"


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


class CapitalRecoveryAction(StrEnum):
    RECONCILE_ORIGINAL_OPERATION = "reconcile_original_operation"
    HOLD_AND_REVIEW = "hold_and_review"


class CapitalAlertKind(StrEnum):
    RECONCILIATION_REQUIRED = "reconciliation_required"
    MANUAL_REVIEW = "manual_review"


class CapitalAlertSeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


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
    desired_available: Quantity
    required_by: datetime
    expires_at: datetime
    priority: FundingPriority = FundingPriority.NORMAL
    confidence: Rate = Rate("1")
    strategy_decision_id: str | None = None
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        objective_id = self.objective_id.strip()
        if not objective_id:
            raise ValueError("Funding objective id is required")
        if self.version <= 0:
            raise ValueError("Funding objective version must be positive")
        desired_available = Quantity(self.desired_available)
        required_by = _utc(self.required_by, "required_by")
        expires_at = _utc(self.expires_at, "expires_at")
        if expires_at < required_by:
            raise ValueError("Funding objective cannot expire before required_by")
        observed_at = (
            _utc(self.observed_at, "observed_at")
            if self.observed_at is not None
            else None
        )
        if observed_at is not None and observed_at > required_by:
            raise ValueError("Funding objective cannot be observed after required_by")
        confidence = Rate(self.confidence)
        if not Rate("0") <= confidence <= Rate("1"):
            raise ValueError("Funding objective confidence must be between 0 and 1")
        decision_id = self.strategy_decision_id
        if decision_id is not None and not decision_id.strip():
            raise ValueError("strategy_decision_id cannot be blank")
        object.__setattr__(self, "objective_id", objective_id)
        object.__setattr__(self, "desired_available", desired_available)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "required_by", required_by)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "observed_at", observed_at)


@dataclass(frozen=True, slots=True)
class FundingForecastObservation:
    """Strategy-side forecast evidence converted into a Capital objective."""

    forecast_id: str
    version: int
    source: FundingForecastSource
    destination: FundingLocation
    predicted_required_available: Quantity
    observed_at: datetime
    required_by: datetime
    expires_at: datetime
    priority: FundingPriority = FundingPriority.NORMAL
    confidence: Rate = Rate("1")
    evidence_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        forecast_id = self.forecast_id.strip()
        if not forecast_id:
            raise ValueError("Funding forecast id is required")
        if self.version <= 0:
            raise ValueError("Funding forecast version must be positive")
        predicted = Quantity(self.predicted_required_available)
        source = FundingForecastSource(self.source)
        observed_at = _utc(self.observed_at, "observed_at")
        required_by = _utc(self.required_by, "required_by")
        expires_at = _utc(self.expires_at, "expires_at")
        if not observed_at <= required_by <= expires_at:
            raise ValueError(
                "Funding forecast requires observed_at <= required_by <= expires_at"
            )
        confidence = Rate(self.confidence)
        if not Rate("0") <= confidence <= Rate("1"):
            raise ValueError("Funding forecast confidence must be between 0 and 1")
        references = tuple(value.strip() for value in self.evidence_references)
        if any(not value for value in references):
            raise ValueError("Funding forecast evidence references cannot be blank")
        object.__setattr__(self, "forecast_id", forecast_id)
        object.__setattr__(self, "predicted_required_available", predicted)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "required_by", required_by)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "evidence_references", references)

    @classmethod
    def from_historical_peak(
        cls,
        *,
        forecast_id: str,
        version: int,
        destination: FundingLocation,
        observed_samples: tuple[Quantity, ...],
        observed_at: datetime,
        required_by: datetime,
        expires_at: datetime,
        safety_buffer: Quantity = Quantity("0"),
        priority: FundingPriority = FundingPriority.NORMAL,
        confidence: Rate = Rate("1"),
        evidence_references: tuple[str, ...] = (),
    ) -> "FundingForecastObservation":
        if not observed_samples:
            raise ValueError("Historical funding forecast requires observed samples")
        samples = tuple(Quantity(value) for value in observed_samples)
        buffer = Quantity(safety_buffer)
        return cls(
            forecast_id=forecast_id,
            version=version,
            source=FundingForecastSource.HISTORICAL_PEAK,
            destination=destination,
            predicted_required_available=max(samples) + buffer,
            observed_at=observed_at,
            required_by=required_by,
            expires_at=expires_at,
            priority=priority,
            confidence=confidence,
            evidence_references=evidence_references,
        )

    def to_objective(self) -> FundingObjective:
        return FundingObjective(
            objective_id=f"forecast:{self.forecast_id}",
            version=self.version,
            destination=self.destination,
            desired_available=self.predicted_required_available,
            required_by=self.required_by,
            expires_at=self.expires_at,
            priority=self.priority,
            confidence=self.confidence,
            strategy_decision_id=(
                f"forecast:{self.source.value}:{self.forecast_id}:{self.version}"
            ),
            observed_at=self.observed_at,
        )


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
    observed_shortfall: Quantity
    observed_at: datetime
    required_by: datetime
    expires_at: datetime
    account_watermark: int
    risk_watermark: int
    destination_lease_fence: str
    priority: FundingPriority = FundingPriority.NORMAL
    confidence: Rate = Rate("1")
    causal_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.demand_id.strip() or not self.idempotency_key.strip():
            raise ValueError("Capital demand identity is required")
        observed_shortfall = Quantity(self.observed_shortfall)
        if observed_shortfall.is_zero:
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
        confidence = Rate(self.confidence)
        if not Rate("0") <= confidence <= Rate("1"):
            raise ValueError("Capital demand confidence must be between 0 and 1")
        object.__setattr__(self, "observed_shortfall", observed_shortfall)
        object.__setattr__(self, "confidence", confidence)
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
    desired_available: QuantityLike


@dataclass(frozen=True, slots=True)
class CapitalAvailability:
    capital_group_id: str | None
    readiness: CapitalReadiness
    location: FundingLocation | None = None
    policy_minimum: QuantityLike | None = None
    policy_default_target: QuantityLike | None = None
    policy_maximum: QuantityLike | None = None
    policy_version: int | None = None
    active_objective_ids: tuple[str, ...] = ()
    active_demand_ids: tuple[str, ...] = ()
    funding_horizons: tuple[CapitalFundingHorizon, ...] = ()
    desired_target: QuantityLike | None = None
    observed_available: QuantityLike | None = None
    effective_target: QuantityLike | None = None
    deficit: QuantityLike | None = None
    account_watermark: int | None = None
    risk_policy_version: int | None = None
    risk_watermark: int | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CapitalRecoveryAlert:
    alert_id: str
    plan_id: str
    operation_id: str | None
    kind: CapitalAlertKind
    severity: CapitalAlertSeverity
    recovery_action: CapitalRecoveryAction
    message: str
    opened_at: datetime


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"Funding objective {name} must be timezone-aware")
    return value.astimezone(timezone.utc)
