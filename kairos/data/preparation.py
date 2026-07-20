from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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


class DataPreparationService:
    def __init__(self, client: ResearchDataClient) -> None:
        self.client = client

    def prepare(self, dataset, *, start: datetime, end: datetime, minimum_quality: QualityLevel,
                provider: str | None = None, venue: str | None = None,
                acquire_missing: bool = False, promote: bool = False,
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
        if _quality_rank(assessment.level) < _quality_rank(minimum_quality):
            raise RuntimeError(
                f"release {release.release_id} reached {assessment.level.value}; requested {minimum_quality.value}"
            )
        release = self.client.catalog.release(release.release_id)
        target = _target_status(minimum_quality)
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
            release.quality_level, release.status, plan.complete, acquired, assessment,
        )


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
