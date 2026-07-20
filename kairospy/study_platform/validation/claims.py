from __future__ import annotations

from dataclasses import dataclass

from .contracts import StudyValidationResult, ValidationLevel


_MAXIMUM_CLAIMS = {
    ValidationLevel.L1_DATA: "data can support the registered study question",
    ValidationLevel.L2_SIGNAL: "signal has registered predictive evidence",
    ValidationLevel.L3_MAPPING: "signal can be mapped to a trade proxy",
    ValidationLevel.L4_EXECUTABLE: "historical executable backtest meets its registered gate",
    ValidationLevel.L5_ROBUSTNESS: "out-of-sample and stress evidence meets robustness gates",
    ValidationLevel.L6_LIVE: "paper or limited-live evidence meets deployment gates",
}


@dataclass(frozen=True, slots=True)
class ClaimDecision:
    allowed: bool
    maximum_claim: str
    reasons: tuple[str, ...]


def authorize_claim(result: StudyValidationResult, requested_level: ValidationLevel, *,
                    mentions_cagr: bool=False, mentions_capacity: bool=False) -> ClaimDecision:
    reasons=[]
    if requested_level>result.state.maximum_level:reasons.append("requested claim exceeds maximum validated level")
    if mentions_cagr and result.state.maximum_level<ValidationLevel.L4_EXECUTABLE:reasons.append("CAGR requires an executable account-equity backtest")
    if mentions_capacity and (not result.data_capabilities.quote_size or result.data_capabilities.order_book_depth<2):
        reasons.append("capacity requires quote size and multi-level market depth")
    return ClaimDecision(not reasons,_MAXIMUM_CLAIMS[result.state.maximum_level],tuple(reasons))
