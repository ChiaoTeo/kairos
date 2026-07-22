from __future__ import annotations

from typing import TYPE_CHECKING

from .events import DataWarningRaised, GovernancePayload
from .promotion import PromotionDecision, PromotionEvidence, PromotionError, PromotionPolicy
from .readiness import ReadinessDecision, ReadinessError, ReadinessEvidence, ReadinessStatus, decide_readiness, require_readiness

if TYPE_CHECKING:
    from .audit import GovernanceAudit
    from .artifact import GovernanceRunArtifactWriter, RunArtifact, RunArtifactRepository

__all__ = [
    "DataWarningRaised",
    "GovernanceAudit",
    "GovernanceRunArtifactWriter",
    "GovernancePayload",
    "PromotionDecision",
    "PromotionEvidence",
    "PromotionError",
    "PromotionPolicy",
    "ReadinessDecision",
    "ReadinessError",
    "ReadinessEvidence",
    "ReadinessStatus",
    "RunArtifact",
    "RunArtifactRepository",
    "audit_governance",
    "decide_readiness",
    "require_readiness",
]


def __getattr__(name: str):
    if name in {"GovernanceAudit", "audit_governance"}:
        from .audit import GovernanceAudit, audit_governance

        return {"GovernanceAudit": GovernanceAudit, "audit_governance": audit_governance}[name]
    if name in {"GovernanceRunArtifactWriter", "RunArtifact", "RunArtifactRepository"}:
        from .artifact import GovernanceRunArtifactWriter, RunArtifact, RunArtifactRepository

        return {
            "GovernanceRunArtifactWriter": GovernanceRunArtifactWriter,
            "RunArtifact": RunArtifact,
            "RunArtifactRepository": RunArtifactRepository,
        }[name]
    raise AttributeError(name)
