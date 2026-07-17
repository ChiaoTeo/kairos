from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from .catalog import DataCatalog
from .models import DatasetStatus, QualityLevel


@dataclass(frozen=True, slots=True)
class GovernedArtifactAudit:
    artifact: str
    passed: bool
    checked_release_ids: tuple[str, ...]
    violations: tuple[str, ...]
    report_hash: str


def audit_governed_artifact(lake_root: str | Path, artifact: str | Path) -> GovernedArtifactAudit:
    path = Path(artifact)
    payload = json.loads(path.read_text(encoding="utf-8"))
    inputs = _consumed_inputs(payload)
    violations = []
    checked = []
    catalog = DataCatalog(lake_root)
    if not inputs:
        violations.append("artifact does not declare governed consumed_inputs")
    for item in inputs:
        release_id = str(item.get("release_id", ""))
        expected_hash = str(item.get("content_hash", ""))
        if not release_id or not expected_hash:
            violations.append("consumed input requires release_id and content_hash")
            continue
        try:
            release = catalog.release(release_id)
        except LookupError:
            violations.append(f"unknown consumed release: {release_id}")
            continue
        checked.append(release_id)
        if release.content_hash != expected_hash:
            violations.append(f"content hash mismatch: {release_id}")
        if release.quality_level not in {QualityLevel.BACKTEST, QualityLevel.PRODUCTION}:
            violations.append(f"consumed release is below Q3: {release_id}={release.quality_level.value}")
        if release.status not in {DatasetStatus.APPROVED_FOR_BACKTEST, DatasetStatus.APPROVED_FOR_PRODUCTION}:
            violations.append(f"consumed release is not backtest-approved: {release_id}={release.status.value}")
    material = {"artifact": str(path), "checked_release_ids": checked, "violations": violations}
    report_hash = sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return GovernedArtifactAudit(str(path), not violations, tuple(checked), tuple(violations), report_hash)


def _consumed_inputs(payload: dict[str, object]) -> tuple[dict[str, object], ...]:
    explicit = payload.get("consumed_inputs")
    if isinstance(explicit, list):
        return tuple(item for item in explicit if isinstance(item, dict))
    singular = payload.get("input")
    if isinstance(singular, dict):
        return (singular,)
    return ()
