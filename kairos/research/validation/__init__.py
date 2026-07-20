"""Governed research validation contracts and gates."""

from .artifacts import ValidationArtifactWriter
from .audit import GovernanceAudit, audit_governance
from .claims import ClaimDecision, authorize_claim
from .gates import GateDecision, GateRequirement, ValidationGate
from .data_gaps import build_data_gap_plan
from .contracts import (
    CapitalSpec,
    DataCapabilities,
    DataGap,
    DataGapPlan,
    EvidenceStatus,
    ExecutionArchetype,
    OutOfSampleEvidence,
    ProductProtocol,
    ResearchValidationResult,
    ReturnDriver,
    SampleSufficiency,
    StudyRegistration,
    ValidationLevel,
    ValidationState,
)
from .protocols import ProtocolDecision, validate_product_protocol, validate_return_driver_protocol
from .samples import assess_sample_sufficiency, approximate_required_samples, overlap_adjusted_effective_samples
from .test_windows import TestWindowRegistry, TestWindowUse
from .bootstrap import block_bootstrap_mean_ci,newey_west_mean_t
from .predictability import PredictabilityResult,validate_predictability
from .report import render_validation_report
from .robustness import RobustnessResult,assess_robustness
from .split import TimeSplit,chronological_split,walk_forward_splits

__all__ = [
    "CapitalSpec", "ClaimDecision", "DataCapabilities", "DataGap", "DataGapPlan", "EvidenceStatus",
    "ExecutionArchetype", "GateDecision", "GateRequirement", "GovernanceAudit", "OutOfSampleEvidence",
    "PredictabilityResult", "ProductProtocol", "ProtocolDecision", "ResearchValidationResult", "ReturnDriver", "RobustnessResult", "SampleSufficiency",
    "StudyRegistration", "TestWindowRegistry", "TestWindowUse", "TimeSplit", "ValidationArtifactWriter", "ValidationGate", "ValidationLevel",
    "ValidationState", "assess_sample_sufficiency", "approximate_required_samples", "audit_governance",
    "assess_robustness", "authorize_claim", "block_bootstrap_mean_ci", "build_data_gap_plan", "chronological_split",
    "newey_west_mean_t", "overlap_adjusted_effective_samples", "render_validation_report", "validate_predictability", "validate_product_protocol",
    "validate_return_driver_protocol",
    "walk_forward_splits",
]
