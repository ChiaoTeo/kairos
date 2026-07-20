from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from kairos.domain.strategy_contract import StrategyLifecycle


@dataclass(frozen=True,slots=True)
class PromotionGateDecision:
    passed: bool
    target: StrategyLifecycle
    reasons: tuple[str,...]


def evaluate_promotion_artifacts(target: StrategyLifecycle,results: tuple[dict,...]) -> PromotionGateDecision:
    reasons=[]
    states=[value.get("state",{}) for value in results]
    if target is StrategyLifecycle.RESEARCH_VALIDATED:
        if not any(state.get("maximum_level",0)>=2 and state.get("signal_status")=="SUPPORTED" for state in states):
            reasons.append("research promotion requires supported L2 signal evidence")
    elif target is StrategyLifecycle.TRADE_PROXY_VALIDATED:
        if not any(state.get("maximum_level",0)>=3 and state.get("strategy_status") in ("TRADE_PROXY_ONLY","SUPPORTED") for state in states):
            reasons.append("trade-proxy promotion requires L3 mapping evidence")
    elif target is StrategyLifecycle.EXECUTABLE_BACKTEST_VALIDATED:
        if not any(state.get("maximum_level",0)>=4 and state.get("execution_status")=="SUPPORTED" and state.get("strategy_status")=="SUPPORTED" for state in states):
            reasons.append("executable promotion requires supported L4 strategy and execution evidence")
    elif target is StrategyLifecycle.ROBUSTNESS_VALIDATED:
        if not any(state.get("maximum_level",0)>=5 and value.get("out_of_sample")=="decision_oos" for state,value in zip(states,results)):
            reasons.append("robustness promotion requires decision-OOS L5 evidence")
    elif target is StrategyLifecycle.PAPER_APPROVED:
        if not any(_is_l5_robustness_evidence(value) for value in results):
            reasons.append("paper approval requires decision-OOS L5 robustness evidence")
        if not any(_is_paper_readiness_evidence(value) for value in results):
            reasons.append("paper approval requires explicit Paper/Testnet readiness evidence; local fixture evidence is not enough")
    elif target in (StrategyLifecycle.LIVE_LIMITED,StrategyLifecycle.LIVE_APPROVED):
        if not any(_is_external_soak_evidence(value) for value in results):
            reasons.append("live promotion requires passed external Paper/Testnet/Live soak evidence")
    else:reasons.append("target is not an evidence promotion stage")
    return PromotionGateDecision(not reasons,target,tuple(reasons))


def _is_l5_robustness_evidence(value: dict) -> bool:
    state = value.get("state", {})
    return (
        isinstance(state, dict)
        and state.get("maximum_level", 0) >= 5
        and value.get("out_of_sample") == "decision_oos"
        and not _is_local_or_synthetic(value)
    )


def _is_paper_readiness_evidence(value: dict) -> bool:
    kind = value.get("kind") or value.get("artifact_type")
    checks = value.get("checks", {})
    environment = str(value.get("environment", "")).lower()
    return (
        kind in ("paper_readiness", "paper_testnet_readiness", "runtime_l4_preflight")
        and environment in ("paper", "testnet")
        and bool(value.get("ready", value.get("passed", False)))
        and _has_valid_audit_hash(value)
        and isinstance(checks, dict)
        and all(bool(checks.get(name)) for name in (
            "environment_compatible", "external_connection_ready", "instrument_listing_ready",
        ))
    )


def _is_external_soak_evidence(value: dict) -> bool:
    kind = value.get("kind") or value.get("artifact_type") or value.get("schema")
    acceptance = value.get("acceptance", {})
    environment = str(value.get("environment", "")).lower()
    return (
        kind in ("runtime_l4_soak", "l4_soak", "runtime_soak")
        and environment in ("paper", "testnet", "live")
        and bool(value.get("passed"))
        and _has_valid_audit_hash(value)
        and isinstance(acceptance, dict)
        and all(bool(acceptance.get(name)) for name in (
            "duration_met", "all_cycles_healthy", "no_critical_alerts",
            "restart_drill_passed", "kill_switch_drill_passed",
        ))
        and not _is_local_or_synthetic(value)
    )


def _is_local_or_synthetic(value: dict) -> bool:
    scope = str(value.get("evidence_scope", value.get("scope", ""))).lower()
    environment = str(value.get("environment", "")).lower()
    source = str(value.get("source", "")).lower()
    return (
        bool(value.get("synthetic") or value.get("fixture") or value.get("local_only"))
        or scope in ("fixture", "synthetic", "local", "local_acceptance")
        or environment in ("simulated", "fixture")
        or source in ("fixture", "synthetic")
    )


def _has_valid_audit_hash(value: dict) -> bool:
    expected = value.get("audit_hash")
    if not isinstance(expected, str) or len(expected) != 64:
        return False
    material = {key: item for key, item in value.items() if key not in {"artifact", "audit_hash"}}
    actual = sha256(json.dumps(
        material, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str,
    ).encode()).hexdigest()
    return actual == expected
