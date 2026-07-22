"""Optional risk policy extensions for concrete product or strategy archetypes."""

from .covered_call import (
    CoveredCallCollateralEvidence,
    CoveredCallCollateralRequest,
    covered_call_collateral_evidence,
    validate_covered_call_collateral,
)

__all__ = [
    "CoveredCallCollateralEvidence",
    "CoveredCallCollateralRequest",
    "covered_call_collateral_evidence",
    "validate_covered_call_collateral",
]
