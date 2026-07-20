from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from kairospy.configuration import DEFAULT_LAKE_ROOT
from kairospy.data.catalog import DataCatalog


@dataclass(frozen=True, slots=True)
class GovernanceAudit:
    passed: bool
    checked_datasets: int
    checked_studies: int
    checked_strategies: int
    violations: tuple[str, ...]


def audit_governance(root: str | Path = DEFAULT_LAKE_ROOT, *, ignored_studies: tuple[str, ...] = ("btc_options_study_summary",)) -> GovernanceAudit:
    root=Path(root);violations=[];datasets=studies=strategies=0;study_versions=[]
    catalog=DataCatalog(root)
    for release in catalog.releases():
        directory=root/release.relative_path
        if not (directory/"manifest.json").exists():continue
        datasets+=1
        for name in ("schema.json","lineage.json","coverage.json","quality.json","manifest.json",
                     "capabilities.json","usage.json","release.json"):
            if not (directory/name).exists():violations.append(f"dataset {release.release_id} missing {name}")
    studies_root=root/"studies"
    if studies_root.exists():
        for directory in sorted(path for path in studies_root.iterdir() if path.is_dir() and path.name not in ignored_studies):
            source_result=directory/"results.json"
            if not source_result.exists():continue
            studies+=1;versions=[path for path in directory.iterdir() if path.is_dir() and (path/"study_spec.json").exists()]
            if not versions:
                violations.append(f"study {directory.name} has no governed version");continue
            for version in versions:
                study_versions.append((directory.name,version.name));_audit_study_version(version,violations)
    if study_versions:
        registry=studies_root/"test_window_registry.jsonl"
        if not registry.exists():violations.append("study test-window registry is missing")
        else:
            uses={(item.get("study_id"),item.get("version")) for line in registry.read_text(encoding="utf-8").splitlines() if line for item in (json.loads(line),)}
            for key in study_versions:
                if key not in uses:violations.append(f"study {key[0]}/{key[1]} missing global test-window usage")
    strategy_root=root/"strategies"
    if strategy_root.exists():
        for strategy in (path for path in strategy_root.iterdir() if path.is_dir()):
            versions=sorted(path.parent for path in strategy.glob("*/manifest.json"))
            for version in versions:
                strategies+=1;_audit_strategy_version(version,violations,enforce_promotion=version==versions[-1])
    return GovernanceAudit(not violations,datasets,studies,strategies,tuple(violations))


def _audit_study_version(directory: Path,violations: list[str]):
    required=("study_spec.json","data_capabilities.json","data_quality.json","sample_sufficiency.json","data_gap_plan.json",
              "results.json","REPORT.md","audit.json","test_usage.json")
    for name in required:
        if not (directory/name).exists():violations.append(f"study {directory.parent.name}/{directory.name} missing {name}")
    result_path=directory/"results.json"
    if result_path.exists():
        level=json.loads(result_path.read_text(encoding="utf-8")).get("state",{}).get("maximum_level",1)
        if level>=3:
            for name in ("capital_spec.json","execution_spec.json","trades.json","risk_decomposition.json","equity_curve.json"):
                if not (directory/name).exists():violations.append(f"strategy study {directory.parent.name}/{directory.name} missing {name}")
    if not (directory/"audit.json").exists():return
    try:audit=json.loads((directory/"audit.json").read_text(encoding="utf-8"))
    except (ValueError,OSError) as error:
        violations.append(f"study {directory.parent.name}/{directory.name} invalid audit: {error}");return
    for name,expected in audit.get("artifact_hashes",{}).items():
        path=directory/name
        if not path.exists():violations.append(f"audited artifact missing: {path}");continue
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        if actual!=expected:violations.append(f"artifact hash mismatch: {path}")


def _audit_strategy_version(directory: Path,violations: list[str],*,enforce_promotion: bool):
    lifecycle=None
    if (directory/"strategy_spec.json").exists():
        lifecycle=json.loads((directory/"strategy_spec.json").read_text(encoding="utf-8")).get("lifecycle")
    required=["strategy_spec.json","execution_policy.json","manifest.json"]
    if lifecycle not in (None,"DRAFT"):required.append("promotions.jsonl")
    for name in required:
        if not (directory/name).exists():violations.append(f"strategy {directory.parent.name}/{directory.name} missing {name}")
    if not (directory/"manifest.json").exists():return
    manifest=json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
    for name,expected in manifest.get("files",{}).items():
        path=directory/name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            violations.append(f"strategy artifact hash mismatch: {path}")
    if enforce_promotion and lifecycle not in (None,"DRAFT") and (directory/"promotions.jsonl").exists():
        records=[json.loads(line) for line in (directory/"promotions.jsonl").read_text(encoding="utf-8").splitlines() if line]
        if not records or records[-1].get("evidence",{}).get("gate_passed") is not True:
            violations.append(f"strategy {directory.parent.name}/{directory.name} latest promotion lacks a passed semantic gate")
        elif records[-1].get("to")!=lifecycle:
            violations.append(f"strategy {directory.parent.name}/{directory.name} lifecycle differs from latest promotion")
