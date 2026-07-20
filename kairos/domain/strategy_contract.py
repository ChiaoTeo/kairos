from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from .intent import Intent
from .product import ProductType


class StrategyLifecycle(StrEnum):
    DRAFT = "DRAFT"
    STUDY_VALIDATED = "STUDY_VALIDATED"
    RESEARCH_VALIDATED = "STUDY_VALIDATED"
    TRADE_PROXY_VALIDATED = "TRADE_PROXY_VALIDATED"
    EXECUTABLE_BACKTEST_VALIDATED = "EXECUTABLE_BACKTEST_VALIDATED"
    ROBUSTNESS_VALIDATED = "ROBUSTNESS_VALIDATED"
    PAPER_APPROVED = "PAPER_APPROVED"
    LIVE_LIMITED = "LIVE_LIMITED"
    LIVE_APPROVED = "LIVE_APPROVED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"

    @classmethod
    def _missing_(cls, value: object):
        if str(value) == "RESEARCH_VALIDATED":
            return cls.STUDY_VALIDATED
        return None


_PROMOTIONS = {
    StrategyLifecycle.DRAFT: StrategyLifecycle.STUDY_VALIDATED,
    StrategyLifecycle.STUDY_VALIDATED: StrategyLifecycle.TRADE_PROXY_VALIDATED,
    StrategyLifecycle.TRADE_PROXY_VALIDATED: StrategyLifecycle.EXECUTABLE_BACKTEST_VALIDATED,
    StrategyLifecycle.EXECUTABLE_BACKTEST_VALIDATED: StrategyLifecycle.ROBUSTNESS_VALIDATED,
    StrategyLifecycle.ROBUSTNESS_VALIDATED: StrategyLifecycle.PAPER_APPROVED,
    StrategyLifecycle.PAPER_APPROVED: StrategyLifecycle.LIVE_LIMITED,
    StrategyLifecycle.LIVE_LIMITED: StrategyLifecycle.LIVE_APPROVED,
}


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    version: str
    lifecycle: StrategyLifecycle
    products: tuple[ProductType, ...]
    strategy_archetypes: tuple[str, ...]
    return_drivers: tuple[str, ...]
    risk_drivers: tuple[str, ...]
    universe: tuple[tuple[str, Any], ...]
    features: tuple[str, ...]
    signal: tuple[tuple[str, Any], ...]
    portfolio_construction: tuple[tuple[str, Any], ...]
    entry_rules: tuple[str, ...]
    exit_rules: tuple[str, ...]
    rebalance_rules: tuple[str, ...]
    risk_budget_fraction: Decimal
    required_data_capabilities: tuple[str, ...]
    required_execution_capabilities: tuple[str, ...]
    research_spec_hash: str

    def __post_init__(self) -> None:
        if not self.strategy_id or not self.version or not self.products:
            raise ValueError("strategy identity, version, and products are required")
        if not Decimal("0") < self.risk_budget_fraction <= Decimal("1"):
            raise ValueError("strategy risk budget must be in (0, 1]")
        if not self.research_spec_hash:
            raise ValueError("research spec hash is required")

    @property
    def spec_hash(self) -> str:
        payload = _jsonable(asdict(self))
        payload.pop("lifecycle", None)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def promote(self, target: StrategyLifecycle) -> "StrategySpec":
        if target in (StrategyLifecycle.SUSPENDED, StrategyLifecycle.RETIRED):
            return _replace_lifecycle(self, target)
        expected = _PROMOTIONS.get(self.lifecycle)
        if target is not expected:
            raise ValueError(f"invalid strategy promotion: {self.lifecycle} -> {target}")
        return _replace_lifecycle(self, target)


@dataclass(frozen=True, slots=True)
class EconomicIntent:
    strategy_id: str
    strategy_version: str
    strategy_spec_hash: str
    decision_id: UUID
    decision_time: datetime
    valid_until: datetime
    intents: tuple[Intent, ...]
    risk_budget: Decimal
    urgency: str
    execution_policy_id: str
    feature_snapshot_hash: str
    diagnostics: tuple[tuple[str, Any], ...] = ()
    hedge_requirements: tuple[tuple[str, Any], ...] = ()
    atomicity_preference: str = "atomic"

    def __post_init__(self) -> None:
        if self.decision_time.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("economic intent times must be timezone-aware")
        if self.valid_until <= self.decision_time:
            raise ValueError("economic intent validity must end after decision time")
        if self.risk_budget <= 0:
            raise ValueError("economic intent risk budget must be positive")
        if not self.intents:
            raise ValueError("economic intent must contain at least one domain intent")
        for intent in self.intents:
            if intent.strategy_id != self.strategy_id:
                raise ValueError("all domain intents must belong to the enclosing strategy")

    @classmethod
    def create(cls, *, strategy: StrategySpec, decision_time: datetime, valid_until: datetime,
               intents: tuple[Intent, ...], risk_budget: Decimal, urgency: str,
               execution_policy_id: str, feature_snapshot_hash: str,
               diagnostics: tuple[tuple[str, Any], ...] = (),
               hedge_requirements: tuple[tuple[str, Any], ...] = (),
               atomicity_preference: str = "atomic") -> "EconomicIntent":
        material = {
            "strategy_id": strategy.strategy_id,
            "strategy_version": strategy.version,
            "strategy_spec_hash": strategy.spec_hash,
            "decision_time": decision_time,
            "valid_until": valid_until,
            "intents": intents,
            "risk_budget": risk_budget,
            "urgency": urgency,
            "execution_policy_id": execution_policy_id,
            "feature_snapshot_hash": feature_snapshot_hash,
            "diagnostics": diagnostics,
            "hedge_requirements": hedge_requirements,
            "atomicity_preference": atomicity_preference,
        }
        encoded = json.dumps(_jsonable(material), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        decision_id = uuid5(NAMESPACE_URL, "kairos:economic-intent:" + hashlib.sha256(encoded.encode()).hexdigest())
        return cls(strategy.strategy_id, strategy.version, strategy.spec_hash, decision_id, decision_time,
                   valid_until, intents, risk_budget, urgency, execution_policy_id,
                   feature_snapshot_hash, diagnostics, hedge_requirements, atomicity_preference)


def _replace_lifecycle(spec: StrategySpec, lifecycle: StrategyLifecycle) -> StrategySpec:
    values = asdict(spec); values["lifecycle"] = lifecycle
    values["products"] = spec.products
    return StrategySpec(**values)


def _jsonable(value):
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (UUID, datetime)):
        return str(value)
    return value
