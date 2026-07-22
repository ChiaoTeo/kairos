from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class ReadinessStatus(StrEnum):
    PASS = "pass"
    DEGRADED = "degraded"
    FAIL = "fail"


class ReadinessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReadinessEvidence:
    """Governance evidence for deciding whether a run profile may start."""

    profile: str
    status: ReadinessStatus | str
    required_ports: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    evidence_refs: Mapping[str, str] = field(default_factory=dict)
    account_binding: str | None = None
    connector_id: str | None = None

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("readiness evidence requires a profile")
        status = ReadinessStatus(self.status)
        object.__setattr__(self, "status", status)
        _require_clean_tuple("required_ports", self.required_ports)
        _require_clean_tuple("reason_codes", self.reason_codes)
        if status in {ReadinessStatus.FAIL, ReadinessStatus.DEGRADED} and not self.reason_codes:
            raise ValueError("non-pass readiness evidence requires reason codes")
        for key, value in self.evidence_refs.items():
            if not str(key).strip() or not str(value).strip():
                raise ValueError("readiness evidence refs require non-empty keys and values")

    @property
    def passed(self) -> bool:
        return self.status is ReadinessStatus.PASS

    @property
    def degraded(self) -> bool:
        return self.status is ReadinessStatus.DEGRADED

    @property
    def blocks_start(self) -> bool:
        return self.status is ReadinessStatus.FAIL


@dataclass(frozen=True, slots=True)
class ReadinessDecision:
    profile: str
    status: ReadinessStatus
    evidence: tuple[ReadinessEvidence, ...]
    reason_codes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.status is ReadinessStatus.PASS

    @property
    def blocks_start(self) -> bool:
        return self.status is ReadinessStatus.FAIL


def decide_readiness(profile: str, evidence: tuple[ReadinessEvidence, ...]) -> ReadinessDecision:
    if not profile.strip():
        raise ValueError("readiness decision requires a profile")
    if not evidence:
        return ReadinessDecision(profile, ReadinessStatus.FAIL, (), ("missing_readiness_evidence",))
    for item in evidence:
        if item.profile != profile:
            raise ValueError("readiness evidence profile mismatch")
    if any(item.status is ReadinessStatus.FAIL for item in evidence):
        status = ReadinessStatus.FAIL
    elif any(item.status is ReadinessStatus.DEGRADED for item in evidence):
        status = ReadinessStatus.DEGRADED
    else:
        status = ReadinessStatus.PASS
    reason_codes = tuple(dict.fromkeys(code for item in evidence for code in item.reason_codes))
    return ReadinessDecision(profile, status, evidence, reason_codes)


def require_readiness(
    profile: str,
    evidence: tuple[ReadinessEvidence, ...],
    *,
    allow_degraded: bool = False,
) -> ReadinessDecision:
    decision = decide_readiness(profile, evidence)
    if decision.blocks_start or (decision.status is ReadinessStatus.DEGRADED and not allow_degraded):
        reasons = ", ".join(decision.reason_codes or (decision.status.value,))
        raise ReadinessError(f"{profile} readiness failed: {reasons}")
    return decision


def _require_clean_tuple(name: str, values: tuple[str, ...]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates")
    if any(not str(value).strip() for value in values):
        raise ValueError(f"{name} must not contain empty values")
