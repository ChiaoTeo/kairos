from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .catalog import DataCatalog
from .contracts import DatasetRelease, DatasetStatus, QualityLevel


REQUIRED_RELEASE_DOCUMENTS = (
    "schema", "lineage", "coverage", "quality", "manifest", "capabilities", "usage", "release",
)


@dataclass(frozen=True, slots=True)
class DataDiagnosticIssue:
    code: str
    severity: str
    message: str
    logical_key: str | None = None
    release_id: str | None = None


class DataDiagnosticsService:
    def __init__(self, root: str | Path = "data") -> None:
        self.root = Path(root)
        self.catalog = DataCatalog(self.root)

    def audit(self) -> dict[str, object]:
        issues: list[DataDiagnosticIssue] = []
        products = self.catalog.products()
        releases = self.catalog.releases()
        for product in products:
            key = str(product.key)
            if not product.description.strip():
                issues.append(DataDiagnosticIssue("missing_product_description", "error", "product description is required", key))
            if not product.owner:
                issues.append(DataDiagnosticIssue("missing_product_owner", "error", "product owner is required", key))
            if not product.dimensions:
                issues.append(DataDiagnosticIssue("missing_product_dimensions", "warning", "product dimensions are empty", key))
            if not self.catalog.releases(product):
                issues.append(DataDiagnosticIssue("product_without_release", "warning", "product has no published release", key))
            try:
                self.catalog.product_spec(product)
            except KeyError:
                issues.append(DataDiagnosticIssue(
                    "missing_product_spec", "warning",
                    "product has no unified schema/storage/quality contract", key,
                ))
        for release in releases:
            issues.extend(self._release_issues(release))
        counts = {
            "products": len(products),
            "releases": len(releases),
            "errors": sum(item.severity == "error" for item in issues),
            "warnings": sum(item.severity == "warning" for item in issues),
            "aliases": len(self.catalog.aliases()),
            "quality_levels": {
                level.value: sum(item.quality_level is level for item in releases) for level in QualityLevel
            },
        }
        return {
            "healthy": counts["errors"] == 0,
            "counts": counts,
            "issues": [item.__dict__ if hasattr(item, "__dict__") else {
                "code": item.code, "severity": item.severity, "message": item.message,
                "logical_key": item.logical_key, "release_id": item.release_id,
            } for item in issues],
        }

    def doctor(self, dataset: str) -> dict[str, object]:
        product = self.catalog.product(dataset)
        releases = self.catalog.releases(product)
        issues = []
        for release in releases:
            issues.extend(self._release_issues(release))
        if not releases:
            issues.append(DataDiagnosticIssue(
                "product_without_release", "error", "prepare or acquire a release before querying",
                str(product.key), None,
            ))
        return {
            "logical_key": str(product.key),
            "healthy": not any(item.severity == "error" for item in issues),
            "releases": [release.release_id for release in releases],
            "issues": [{
                "code": item.code, "severity": item.severity, "message": item.message,
                "logical_key": item.logical_key, "release_id": item.release_id,
            } for item in issues],
            "next": self._next_action(product=str(product.key), releases=releases, issues=issues),
        }

    def _release_issues(self, release: DatasetRelease) -> list[DataDiagnosticIssue]:
        key = str(release.product_key)
        result: list[DataDiagnosticIssue] = []
        try:
            spec = self.catalog.product_spec(release.product_key)
        except KeyError:
            spec = None
        if spec is not None and release.storage_kind is not spec.storage_kind:
            result.append(DataDiagnosticIssue(
                "release_storage_kind_mismatch", "error",
                f"release storage {release.storage_kind.value} differs from DataProductContract {spec.storage_kind.value}",
                key, release.release_id,
            ))
        if spec is not None and release.layout_version != spec.layout_version:
            result.append(DataDiagnosticIssue(
                "release_layout_version_mismatch", "error",
                f"release layout {release.layout_version} differs from DataProductContract {spec.layout_version}",
                key, release.release_id,
            ))
        directory = self.root / release.relative_path
        if not directory.exists():
            return [DataDiagnosticIssue(
                "missing_release_path", "error", f"release path does not exist: {release.relative_path}",
                key, release.release_id,
            )]
        if not release.content_hash:
            result.append(DataDiagnosticIssue("missing_content_hash", "error", "release content hash is required", key, release.release_id))
        for name in REQUIRED_RELEASE_DOCUMENTS:
            if not (directory / f"{name}.json").exists():
                result.append(DataDiagnosticIssue(
                    f"missing_{name}", "error", f"release metadata {name}.json is required", key, release.release_id,
                ))
        if release.status is DatasetStatus.APPROVED_FOR_BACKTEST and release.quality_level not in {
            QualityLevel.BACKTEST, QualityLevel.PRODUCTION,
        }:
            result.append(DataDiagnosticIssue(
                "backtest_quality_mismatch", "error", "backtest-approved release must be Q3 or Q4",
                key, release.release_id,
            ))
        if release.status is DatasetStatus.APPROVED_FOR_STUDY and release.quality_level not in {
            QualityLevel.STUDY, QualityLevel.BACKTEST, QualityLevel.PRODUCTION,
        }:
            result.append(DataDiagnosticIssue(
                "study_quality_mismatch", "error", "study-approved release must be Q2, Q3 or Q4",
                key, release.release_id,
            ))
        if release.status is DatasetStatus.APPROVED_FOR_PRODUCTION and release.quality_level is not QualityLevel.PRODUCTION:
            result.append(DataDiagnosticIssue(
                "production_quality_mismatch", "error", "production-approved release must be Q4",
                key, release.release_id,
            ))
        return result

    @staticmethod
    def _next_action(*, product: str, releases: tuple[DatasetRelease, ...],
                     issues: list[DataDiagnosticIssue]) -> str:
        if not releases:
            return f"kairospy data plan --dataset {product} --start <UTC> --end <UTC>"
        missing = [item.code.removeprefix("missing_") for item in issues if item.code.startswith("missing_")]
        if missing:
            return f"reacquire {product}; current releases must be published with complete metadata"
        if any("quality" in item.code for item in issues):
            return f"review quality report and revalidate {product}"
        return f"kairospy data describe --dataset {product}"
