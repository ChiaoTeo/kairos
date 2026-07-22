from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import IntEnum, StrEnum
import hashlib
import json
import math
from typing import Any


class ValidationLevel(IntEnum):
    L1_DATA = 1
    L2_SIGNAL = 2
    L3_MAPPING = 3
    L4_EXECUTABLE = 4
    L5_ROBUSTNESS = 5
    L6_LIVE = 6


class EvidenceStatus(StrEnum):
    NOT_TESTED = "NOT_TESTED"
    READY = "READY"
    DATA_NOT_READY = "DATA_NOT_READY"
    EXPLORATORY = "EXPLORATORY"
    TRADE_PROXY_ONLY = "TRADE_PROXY_ONLY"
    MAKER_FILL_PROXY_ONLY = "MAKER_FILL_PROXY_ONLY"
    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    REJECTED = "REJECTED"


class ProductProtocol(StrEnum):
    SPOT = "spot"
    FUTURE = "future"
    PERPETUAL = "perpetual"
    OPTION = "option"


class ReturnDriver(StrEnum):
    DIRECTION = "direction"
    TREND = "trend"
    MEAN_REVERSION = "mean_reversion"
    CARRY = "carry"
    BASIS = "basis"
    VOLATILITY = "volatility"
    SKEW = "skew"
    TAIL_RISK = "tail_risk"
    LIQUIDITY = "liquidity"


class ExecutionArchetype(StrEnum):
    NONE = "none"
    MAKER = "maker"
    TAKER = "taker"
    HYBRID = "hybrid"


class OutOfSampleEvidence(StrEnum):
    NONE = "none"
    PARAMETER = "parameter_oos"
    TIME = "time_oos"
    DECISION = "decision_oos"


@dataclass(frozen=True, slots=True)
class ValidationState:
    data_status: EvidenceStatus = EvidenceStatus.NOT_TESTED
    signal_status: EvidenceStatus = EvidenceStatus.NOT_TESTED
    execution_status: EvidenceStatus = EvidenceStatus.NOT_TESTED
    strategy_status: EvidenceStatus = EvidenceStatus.NOT_TESTED
    maximum_level: ValidationLevel = ValidationLevel.L1_DATA
    maximum_claim: str = "not tested"


@dataclass(frozen=True, slots=True)
class DataCapabilities:
    dataset_ids: tuple[str, ...]
    event_time: bool = True
    received_time: bool = False
    point_in_time_universe: bool = False
    synchronous_quotes: bool = False
    top_of_book: bool = False
    quote_size: bool = False
    order_book_depth: int = 0
    incremental_order_book: bool = False
    sequence_numbers: bool = False
    trade_events: bool = False
    trade_direction: bool = False
    queue_reconstructable: bool = False
    settlement_price: bool = False
    funding: bool = False
    lifecycle_events: bool = False
    supported_products: tuple[ProductProtocol, ...] = ()
    maximum_validation_level: ValidationLevel = ValidationLevel.L1_DATA

    def __post_init__(self) -> None:
        if not self.dataset_ids:
            raise ValueError("at least one dataset id is required")
        if self.order_book_depth < 0:
            raise ValueError("order book depth cannot be negative")
        if self.queue_reconstructable and not (self.incremental_order_book and self.sequence_numbers and self.trade_events):
            raise ValueError("queue reconstruction requires incremental book, sequence numbers, and trades")
        if self.order_book_depth and not self.top_of_book:
            raise ValueError("order book depth requires top-of-book support")

    def supports_execution(self, archetype: ExecutionArchetype, *, multi_leg: bool = False, capacity: bool = False) -> tuple[bool, tuple[str, ...]]:
        missing: list[str] = []
        if archetype is ExecutionArchetype.NONE:
            return True, ()
        if not self.synchronous_quotes:
            missing.append("synchronous_quotes")
        if not self.top_of_book:
            missing.append("top_of_book")
        if not self.quote_size:
            missing.append("quote_size")
        if capacity and self.order_book_depth < 2:
            missing.append("multi_level_order_book")
        if archetype in (ExecutionArchetype.MAKER, ExecutionArchetype.HYBRID):
            if not self.incremental_order_book:
                missing.append("incremental_order_book")
            if not self.sequence_numbers:
                missing.append("sequence_numbers")
            if not self.trade_events:
                missing.append("trade_events")
            if not self.queue_reconstructable:
                missing.append("queue_reconstructable")
        if multi_leg and not self.synchronous_quotes:
            missing.append("synchronous_multi_leg_quotes")
        return not missing, tuple(dict.fromkeys(missing))


@dataclass(frozen=True, slots=True)
class CapitalSpec:
    initial_equity: Decimal
    base_currency: str
    risk_budget_per_trade: Decimal
    portfolio_risk_limit: Decimal
    margin_model: str
    capital_reinvestment: bool
    allow_overlapping_positions: bool
    idle_cash_return_model: str
    liquidation_policy: str

    def __post_init__(self) -> None:
        if self.initial_equity <= 0:
            raise ValueError("initial equity must be positive")
        if not Decimal("0") < self.risk_budget_per_trade <= Decimal("1"):
            raise ValueError("risk budget must be in (0, 1]")
        if not self.risk_budget_per_trade <= self.portfolio_risk_limit <= Decimal("1"):
            raise ValueError("portfolio risk limit must cover per-trade risk and be <= 1")
        if not self.base_currency or not self.margin_model or not self.liquidation_policy:
            raise ValueError("capital currency, margin model, and liquidation policy are required")


@dataclass(frozen=True, slots=True)
class SampleSufficiency:
    raw_observations: int
    non_overlapping_observations: int
    effective_observations: float
    required_effective_observations: int
    minimum_detectable_effect: float | None = None
    target_power: float = .80
    regimes_observed: tuple[str, ...] = ()
    extreme_events: int = 0

    def __post_init__(self) -> None:
        if min(self.raw_observations, self.non_overlapping_observations, self.required_effective_observations, self.extreme_events) < 0:
            raise ValueError("sample counts cannot be negative")
        if not 0 <= self.effective_observations <= self.raw_observations:
            raise ValueError("effective observations must be between zero and raw observations")
        if not 0 < self.target_power < 1:
            raise ValueError("target power must be in (0, 1)")

    @property
    def ready(self) -> bool:
        return self.effective_observations >= self.required_effective_observations

    @property
    def additional_samples_required(self) -> int:
        return max(0, math.ceil(self.required_effective_observations - self.effective_observations))


@dataclass(frozen=True, slots=True)
class DataGap:
    category: str
    capability: str
    blocks_level: ValidationLevel
    remediation: str


@dataclass(frozen=True, slots=True)
class DataGapPlan:
    gaps: tuple[DataGap, ...]
    collection_frequency: str | None = None
    collection_started_at: str | None = None
    target_samples: int | None = None
    reevaluation_condition: str | None = None

    @property
    def blocked_capabilities(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(gap.capability for gap in self.gaps))


@dataclass(frozen=True, slots=True)
class ExperimentRegistration:
    experiment_id: str
    version: str
    hypothesis: str
    products: tuple[ProductProtocol, ...]
    strategy_archetypes: tuple[str, ...]
    return_drivers: tuple[ReturnDriver, ...]
    risk_drivers: tuple[str, ...]
    execution_archetype: ExecutionArchetype
    development_period: tuple[str, str]
    validation_period: tuple[str, str] | None
    test_period: tuple[str, str]
    features: tuple[str, ...]
    labels: tuple[str, ...]
    horizons_days: tuple[int, ...]
    primary_metric: str
    minimum_effective_samples: int
    acceptance_rule: str
    rejection_rule: str
    required_data_capabilities: tuple[str, ...]
    capital_spec: CapitalSpec | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.version or not self.hypothesis:
            raise ValueError("experiment identity, version, and hypothesis are required")
        if not self.products or not self.return_drivers:
            raise ValueError("product and return-driver declarations are required")
        if self.minimum_effective_samples < 1:
            raise ValueError("minimum effective samples must be positive")
        if any(horizon < 1 for horizon in self.horizons_days):
            raise ValueError("horizons must be positive")
        _validate_period(self.development_period, "development")
        _validate_period(self.test_period, "test")
        if self.validation_period:
            _validate_period(self.validation_period, "validation")

    @property
    def spec_hash(self) -> str:
        payload = _jsonable(asdict(self))
        payload.pop("created_at", None)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ExperimentValidationResult:
    registration: ExperimentRegistration
    state: ValidationState
    data_capabilities: DataCapabilities
    sample_sufficiency: SampleSufficiency
    out_of_sample: OutOfSampleEvidence
    metrics: dict[str, Any]
    limitations: tuple[str, ...] = ()
    data_gap_plan: DataGapPlan = field(default_factory=lambda: DataGapPlan(()))
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def spec_hash(self) -> str:
        return self.registration.spec_hash

    def to_dict(self) -> dict[str, Any]:
        value = _jsonable(asdict(self))
        value["spec_hash"] = self.spec_hash
        value["sample_sufficiency"]["ready"] = self.sample_sufficiency.ready
        value["sample_sufficiency"]["additional_samples_required"] = self.sample_sufficiency.additional_samples_required
        value["data_gap_plan"]["blocked_capabilities"] = list(self.data_gap_plan.blocked_capabilities)
        return value


def _validate_period(period: tuple[str, str], label: str) -> None:
    if len(period) != 2 or period[0] >= period[1]:
        raise ValueError(f"{label} period must use increasing [start, end) values")


def _jsonable(value):
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (StrEnum, IntEnum)):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    return value
