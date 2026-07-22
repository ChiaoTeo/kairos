from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

from kairospy.strategy.contracts import StrategyLifecycle


_PROMOTION_ORDER = (
    StrategyLifecycle.DRAFT,
    StrategyLifecycle.RESEARCH_VALIDATED,
    StrategyLifecycle.TRADE_PROXY_VALIDATED,
    StrategyLifecycle.EXECUTABLE_BACKTEST_VALIDATED,
    StrategyLifecycle.ROBUSTNESS_VALIDATED,
    StrategyLifecycle.PAPER_APPROVED,
    StrategyLifecycle.LIVE_LIMITED,
    StrategyLifecycle.LIVE_APPROVED,
)


class PromotionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    from_stage: StrategyLifecycle | str
    to_stage: StrategyLifecycle | str
    dataset_hash: str
    strategy_hash: str
    config_hash: str
    gate_passed: bool
    evidence_refs: Mapping[str, str] = field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "from_stage", StrategyLifecycle(self.from_stage))
        object.__setattr__(self, "to_stage", StrategyLifecycle(self.to_stage))
        for name in ("dataset_hash", "strategy_hash", "config_hash"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"promotion evidence requires {name}")
        if self.recorded_at.tzinfo is None:
            raise ValueError("promotion evidence recorded_at must be timezone-aware")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("promotion reason codes must not contain duplicates")
        if any(not str(value).strip() for value in self.reason_codes):
            raise ValueError("promotion reason codes must not contain empty values")
        for key, value in self.evidence_refs.items():
            if not str(key).strip() or not str(value).strip():
                raise ValueError("promotion evidence refs require non-empty keys and values")


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    from_stage: StrategyLifecycle
    to_stage: StrategyLifecycle
    approved: bool
    reason_codes: tuple[str, ...]
    evidence: PromotionEvidence


class PromotionPolicy:
    def evaluate(self, evidence: PromotionEvidence) -> PromotionDecision:
        reasons: list[str] = list(evidence.reason_codes)
        if evidence.to_stage in {StrategyLifecycle.SUSPENDED, StrategyLifecycle.RETIRED}:
            transition_allowed = True
        else:
            transition_allowed = _next_stage(evidence.from_stage) is evidence.to_stage
        if not transition_allowed:
            reasons.append("invalid_promotion_transition")
        if not evidence.gate_passed:
            reasons.append("promotion_gate_failed")
        if _requires_live_readiness(evidence.to_stage) and "readiness" not in evidence.evidence_refs:
            reasons.append("missing_readiness_evidence")
        approved = not reasons
        return PromotionDecision(
            evidence.from_stage,
            evidence.to_stage,
            approved,
            tuple(dict.fromkeys(reasons)),
            evidence,
        )

    def require(self, evidence: PromotionEvidence) -> PromotionDecision:
        decision = self.evaluate(evidence)
        if not decision.approved:
            reasons = ", ".join(decision.reason_codes)
            raise PromotionError(f"promotion blocked: {reasons}")
        return decision


def _next_stage(stage: StrategyLifecycle) -> StrategyLifecycle | None:
    try:
        return _PROMOTION_ORDER[_PROMOTION_ORDER.index(stage) + 1]
    except (ValueError, IndexError):
        return None


def _requires_live_readiness(stage: StrategyLifecycle) -> bool:
    return stage in {
        StrategyLifecycle.PAPER_APPROVED,
        StrategyLifecycle.LIVE_LIMITED,
        StrategyLifecycle.LIVE_APPROVED,
    }
