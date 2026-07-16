from __future__ import annotations

from dataclasses import dataclass

from .models import (
    EvidenceStatus, ExecutionArchetype, OutOfSampleEvidence,
    ResearchValidationResult, ValidationLevel,
)
from .protocols import validate_product_protocol, validate_return_driver_protocol


@dataclass(frozen=True, slots=True)
class GateRequirement:
    target_level: ValidationLevel
    minimum_effective_samples: int = 1
    minimum_oos: OutOfSampleEvidence = OutOfSampleEvidence.NONE
    execution_archetype: ExecutionArchetype = ExecutionArchetype.NONE
    multi_leg: bool = False
    require_capital_spec: bool = False
    require_no_data_gaps: bool = False


@dataclass(frozen=True, slots=True)
class GateDecision:
    passed: bool
    target_level: ValidationLevel
    reasons: tuple[str, ...]


class ValidationGate:
    def evaluate(self, result: ResearchValidationResult, requirement: GateRequirement) -> GateDecision:
        reasons: list[str] = []
        if result.state.maximum_level < requirement.target_level:
            reasons.append(f"evidence reaches {result.state.maximum_level.name}, below {requirement.target_level.name}")
        if result.sample_sufficiency.effective_observations < requirement.minimum_effective_samples:
            reasons.append("effective sample size below gate minimum")
        if _oos_rank(result.out_of_sample) < _oos_rank(requirement.minimum_oos):
            reasons.append(f"out-of-sample evidence below {requirement.minimum_oos.value}")
        product = validate_product_protocol(result.registration.products, result.data_capabilities, requirement.target_level)
        reasons.extend(f"missing product capability: {item}" for item in product.missing_capabilities)
        driver = validate_return_driver_protocol(result.registration.return_drivers, result.data_capabilities, requirement.target_level)
        reasons.extend(f"missing return-driver capability: {item}" for item in driver.missing_capabilities)
        if requirement.target_level >= ValidationLevel.L3_MAPPING and result.state.signal_status not in (
            EvidenceStatus.SUPPORTED, EvidenceStatus.EXPLORATORY,
        ):
            reasons.append("signal is neither supported nor explicitly exploratory")
        if requirement.target_level >= ValidationLevel.L4_EXECUTABLE:
            if result.state.execution_status is not EvidenceStatus.SUPPORTED:
                reasons.append("execution evidence is not supported")
            supported, missing = result.data_capabilities.supports_execution(
                requirement.execution_archetype, multi_leg=requirement.multi_leg,
            )
            if not supported:
                reasons.extend(f"missing data capability: {item}" for item in missing)
            if result.registration.capital_spec is None:
                reasons.append("capital spec is required for executable validation")
            blocking=[gap for gap in result.data_gap_plan.gaps if gap.blocks_level<=requirement.target_level]
            if blocking:reasons.append("data gaps block executable validation")
        if requirement.target_level >= ValidationLevel.L5_ROBUSTNESS:
            if result.out_of_sample is not OutOfSampleEvidence.DECISION:
                reasons.append("robustness validation requires decision-OOS evidence")
            if not result.sample_sufficiency.ready:
                reasons.append("robustness validation requires sufficient effective samples")
            robustness=result.metrics.get("robustness",{}) if isinstance(result.metrics,dict) else {}
            for name in ("parameter_stable","regime_stable","stress_cost_passed"):
                if robustness.get(name) is not True:reasons.append(f"robustness check not passed: {name}")
        if requirement.target_level >= ValidationLevel.L6_LIVE:
            deployment=result.metrics.get("deployment",{}) if isinstance(result.metrics,dict) else {}
            for name in ("paper_or_live_evidence","reconciliation_passed","risk_controls_passed"):
                if deployment.get(name) is not True:reasons.append(f"deployment check not passed: {name}")
        if requirement.require_no_data_gaps and result.data_gap_plan.gaps:
            reasons.append("unresolved data gaps remain")
        return GateDecision(not reasons, requirement.target_level, tuple(dict.fromkeys(reasons)))


def _oos_rank(value: OutOfSampleEvidence) -> int:
    return {
        OutOfSampleEvidence.NONE: 0,
        OutOfSampleEvidence.PARAMETER: 1,
        OutOfSampleEvidence.TIME: 2,
        OutOfSampleEvidence.DECISION: 3,
    }[value]
