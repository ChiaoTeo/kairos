from __future__ import annotations

from .claims import authorize_claim
from .contracts import ExperimentValidationResult,ValidationLevel


def render_validation_report(result: ExperimentValidationResult,requested_level: ValidationLevel|None=None) -> str:
    level=requested_level or result.state.maximum_level;claim=authorize_claim(result,level)
    if not claim.allowed:raise ValueError("report claim is not authorized: "+"; ".join(claim.reasons))
    sample=result.sample_sufficiency
    lines=[f"# {result.registration.experiment_id} {result.registration.version}","","## Evidence","",
        f"- Maximum level: `{result.state.maximum_level.name}`",f"- Maximum claim: {claim.maximum_claim}",
        f"- OOS evidence: `{result.out_of_sample.value}`",f"- Effective samples: {sample.effective_observations:.2f}/{sample.required_effective_observations}",
        "","## Multi-dimensional state","",
        f"- Data: `{result.state.data_status.value}`",f"- Signal: `{result.state.signal_status.value}`",
        f"- Execution: `{result.state.execution_status.value}`",f"- Strategy: `{result.state.strategy_status.value}`"]
    if result.data_gap_plan.gaps:
        lines += ["","## Blocking data gaps",""]+[f"- `{gap.capability}`: {gap.remediation}" for gap in result.data_gap_plan.gaps]
    if result.limitations:lines += ["","## Limitations",""]+[f"- {value}" for value in result.limitations]
    return "\n".join(lines)+"\n"
