from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from .catalog import DataCatalog
from .client import DataUnavailableError, ResearchDataClient
from .contracts import DatasetStatus, QualityLevel
from .quality import DatasetQualityService, QualityAssessment


@dataclass(frozen=True, slots=True)
class PreparedDataset:
    logical_key: str
    release_id: str
    content_hash: str
    quality_level: QualityLevel
    status: DatasetStatus
    coverage_complete: bool
    acquired: bool
    assessment: QualityAssessment
    policy: "DataPromotionPolicyResult"


@dataclass(frozen=True, slots=True)
class DataPromotionPolicyResult:
    release_id: str
    requested_quality: QualityLevel
    target_status: DatasetStatus
    passed: bool
    gate_failures: tuple[str, ...]
    diagnostics: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class DataPromotionPolicyProfile:
    name: str
    minimum_assessment_level: QualityLevel
    required_diagnostics: tuple[str, ...] = ()


RESEARCH_DEFAULT_POLICY = DataPromotionPolicyProfile("research-default", QualityLevel.RESEARCH)
BACKTEST_DEFAULT_POLICY = DataPromotionPolicyProfile("backtest-default", QualityLevel.BACKTEST)
PRODUCTION_DEFAULT_POLICY = DataPromotionPolicyProfile("production-default", QualityLevel.PRODUCTION)

DATA_PROMOTION_POLICY_PROFILES: Mapping[str, DataPromotionPolicyProfile] = MappingProxyType({
    RESEARCH_DEFAULT_POLICY.name: RESEARCH_DEFAULT_POLICY,
    BACKTEST_DEFAULT_POLICY.name: BACKTEST_DEFAULT_POLICY,
    PRODUCTION_DEFAULT_POLICY.name: PRODUCTION_DEFAULT_POLICY,
})


class DataPreparationService:
    def __init__(self, client: ResearchDataClient) -> None:
        self.client = client

    def prepare(self, dataset, *, start: datetime, end: datetime, minimum_quality: QualityLevel,
                provider: str | None = None, venue: str | None = None,
                acquire_missing: bool = False, promote: bool = False,
                promotion_policy: DataPromotionPolicyProfile | None = None,
                actor: str = "data-prepare", reason: str = "explicit data preparation") -> PreparedDataset:
        plan = self.client.plan(dataset, start=start, end=end, provider=provider, venue=venue)
        acquired = False
        if not plan.complete:
            if not acquire_missing:
                raise DataUnavailableError(plan)
            self.client.acquire(plan)
            acquired = True
            plan = self.client.plan(dataset, start=start, end=end, provider=provider, venue=venue)
            if not plan.complete:
                raise DataUnavailableError(plan)
        release = self.client.catalog.release(dataset, provider=provider, venue=venue)
        assessment = DatasetQualityService(self.client.root).assess(release.release_id)
        policy_profile = promotion_policy or _policy_profile_from_contract(
            self.client.catalog, release.product_key, minimum_quality,
        )
        policy = evaluate_data_promotion_policy(
            release.release_id, assessment, minimum_quality, profile=policy_profile,
        )
        if not policy.passed:
            raise RuntimeError(policy.reason)
        self.client.catalog = DataCatalog(self.client.root, self.client.catalog.registry_path)
        release = self.client.catalog.release(release.release_id)
        target = policy.target_status
        if promote and _status_rank(release.status) < _status_rank(target):
            while _status_rank(release.status) < _status_rank(target):
                next_status = {
                    DatasetStatus.VALIDATED: DatasetStatus.APPROVED_FOR_RESEARCH,
                    DatasetStatus.APPROVED_FOR_RESEARCH: DatasetStatus.APPROVED_FOR_BACKTEST,
                    DatasetStatus.APPROVED_FOR_BACKTEST: DatasetStatus.APPROVED_FOR_PRODUCTION,
                }.get(release.status)
                if next_status is None:
                    raise RuntimeError(f"cannot promote release from {release.status.value}")
                release = self.client.catalog.promote(
                    release.release_id, next_status, actor=actor, reason=reason,
                )
        if _status_rank(release.status) < _status_rank(target):
            raise PermissionError(
                f"release quality passed but status is {release.status.value}; rerun with explicit promote approval"
            )
        if release.content_hash is None:
            raise ValueError("prepared release requires a content hash")
        return PreparedDataset(
            str(release.product_key), release.release_id, release.content_hash,
            release.quality_level, release.status, plan.complete, acquired, assessment, policy,
        )


def evaluate_data_promotion_policy(
    release_id: str, assessment: QualityAssessment, requested_quality: QualityLevel,
    *, profile: DataPromotionPolicyProfile | None = None,
) -> DataPromotionPolicyResult:
    target = _target_status(requested_quality)
    profile = profile or _default_policy_profile(requested_quality)
    gate_failures = tuple(item.name for item in assessment.checks if item.severity == "gate" and not item.passed)
    diagnostics = tuple(item.name for item in assessment.checks if item.severity == "diagnostic" and not item.passed)
    missing_required_diagnostics = tuple(
        name for name in profile.required_diagnostics
        if not any(item.name == name and item.severity == "diagnostic" and item.passed for item in assessment.checks)
    )
    if gate_failures:
        return DataPromotionPolicyResult(
            release_id, requested_quality, target, False, gate_failures, diagnostics,
            f"release {release_id} failed gate checks: {', '.join(gate_failures)}",
        )
    if missing_required_diagnostics:
        return DataPromotionPolicyResult(
            release_id, requested_quality, target, False, gate_failures, diagnostics,
            f"release {release_id} failed promotion policy {profile.name}: "
            f"{', '.join(missing_required_diagnostics)}",
        )
    if _quality_rank(assessment.level) < _quality_rank(profile.minimum_assessment_level):
        return DataPromotionPolicyResult(
            release_id, requested_quality, target, False, gate_failures, diagnostics,
            f"release {release_id} reached {assessment.level.value}; "
            f"policy {profile.name} requires {profile.minimum_assessment_level.value}",
        )
    return DataPromotionPolicyResult(
        release_id, requested_quality, target, True, gate_failures, diagnostics,
        f"release {release_id} satisfies {requested_quality.value} promotion policy",
    )


def _default_policy_profile(level: QualityLevel) -> DataPromotionPolicyProfile:
    if level is QualityLevel.PRODUCTION:
        return PRODUCTION_DEFAULT_POLICY
    if level is QualityLevel.BACKTEST:
        return BACKTEST_DEFAULT_POLICY
    return RESEARCH_DEFAULT_POLICY


def data_promotion_policy_profile(name: str) -> DataPromotionPolicyProfile:
    try:
        return DATA_PROMOTION_POLICY_PROFILES[name]
    except KeyError as error:
        raise ValueError(f"unknown data promotion policy profile: {name}") from error


def _policy_profile_from_contract(catalog, product_key, level: QualityLevel) -> DataPromotionPolicyProfile | None:
    try:
        capabilities = catalog.product_spec(product_key).capabilities
    except KeyError:
        return None
    raw = capabilities.get("promotion_policy") if isinstance(capabilities, dict) else None
    if not isinstance(raw, dict):
        return None
    level_key = level.value
    policy = raw.get(level_key) or raw.get(level.name) or raw.get(level.name.lower())
    if isinstance(policy, str):
        return data_promotion_policy_profile(policy)
    if not isinstance(policy, dict):
        return None
    builtin = policy.get("profile")
    if isinstance(builtin, str):
        base = data_promotion_policy_profile(builtin)
        name = str(policy.get("name") or base.name)
        minimum = QualityLevel(str(policy.get("minimum_assessment_level", base.minimum_assessment_level.value)))
        required = tuple(str(item) for item in policy.get("required_diagnostics", base.required_diagnostics))
        return DataPromotionPolicyProfile(name, minimum, required)
    minimum = QualityLevel(str(policy.get("minimum_assessment_level", level.value)))
    required = tuple(str(item) for item in policy.get("required_diagnostics", ()))
    name = str(policy.get("name") or f"{product_key}:{level.value}")
    return DataPromotionPolicyProfile(name, minimum, required)


def _quality_rank(level: QualityLevel) -> int:
    return list(QualityLevel).index(level)


def _target_status(level: QualityLevel) -> DatasetStatus:
    if level is QualityLevel.PRODUCTION:
        return DatasetStatus.APPROVED_FOR_PRODUCTION
    if level is QualityLevel.BACKTEST:
        return DatasetStatus.APPROVED_FOR_BACKTEST
    return DatasetStatus.APPROVED_FOR_RESEARCH


def _status_rank(status: DatasetStatus) -> int:
    return {
        DatasetStatus.DRAFT: 0,
        DatasetStatus.REGISTERED: 0,
        DatasetStatus.VALIDATING: 0,
        DatasetStatus.VALIDATED: 1,
        DatasetStatus.APPROVED_FOR_RESEARCH: 2,
        DatasetStatus.APPROVED_FOR_BACKTEST: 3,
        DatasetStatus.APPROVED_FOR_PRODUCTION: 4,
    }.get(status, -1)
