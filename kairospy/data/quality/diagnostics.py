from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from ..catalog import DataCatalog
from ..contracts import DatasetRelease, DatasetStatus, QualityLevel
from .freshness import PAPER_LIVE_FRESHNESS_POLICY, evaluate_live_view_freshness, load_live_view_manifest


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
    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
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
        return DatasetReadinessService(self.root).doctor(dataset)

    def technical_doctor(self, dataset: str) -> dict[str, object]:
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
        if release.status is DatasetStatus.APPROVED_FOR_WORKSPACE and release.quality_level not in {
            QualityLevel.WORKSPACE, QualityLevel.BACKTEST, QualityLevel.PRODUCTION,
        }:
            result.append(DataDiagnosticIssue(
                "workspace_quality_mismatch", "error", "workspace-approved release must be Q2, Q3 or Q4",
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


class DatasetReadinessService:
    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root = Path(root)
        self.catalog = DataCatalog(self.root)

    def doctor(self, dataset: str) -> dict[str, object]:
        product = None
        releases: tuple[DatasetRelease, ...] = ()
        technical_issues: list[DataDiagnosticIssue] = []
        try:
            product = self.catalog.product(dataset)
            releases = self.catalog.releases(product)
        except KeyError:
            product = None
        if product is not None:
            for release in releases:
                technical_issues.extend(DataDiagnosticsService(self.root)._release_issues(release))

        live_views = self._live_views(dataset)
        historical = self._historical_status(dataset, releases, technical_issues)
        live = self._live_status(live_views)
        ready = sorted(set(historical["ready_for"]) | set(live["ready_for"]))
        blocked = sorted(set(historical["blocked_for"]) | set(live["blocked_for"]))
        issues = list(historical["issues"]) + list(live["issues"])
        status = self._overall_status(historical, live, releases, live_views, issues)
        return {
            "dataset": dataset,
            "status": status,
            "healthy": status.startswith("ready"),
            "source_kind": self._source_kind(releases, live_views),
            "time": product.primary_time if product is not None else self._live_primary_time(live_views),
            "historical": historical,
            "live": live,
            "ready_for": ready,
            "blocked_for": blocked,
            "issues": issues,
        }

    def _historical_status(
        self,
        dataset: str,
        releases: tuple[DatasetRelease, ...],
        technical_issues: list[DataDiagnosticIssue],
    ) -> dict[str, object]:
        if not releases:
            return {
                "status": "not_configured",
                "ready_for": [],
                "blocked_for": ["workspace", "backtest"],
                "issues": [],
            }
        blocking_codes = {
            "missing_release_path",
            "missing_content_hash",
            "missing_quality",
            "backtest_quality_mismatch",
            "workspace_quality_mismatch",
            "production_quality_mismatch",
            "release_storage_kind_mismatch",
            "release_layout_version_mismatch",
        }
        errors = [item for item in technical_issues if item.severity == "error" and item.code in blocking_codes]
        if errors:
            return {
                "status": "needs_fix",
                "ready_for": [],
                "blocked_for": ["workspace", "backtest"],
                "issues": [item.code for item in errors],
            }
        best = sorted(releases, key=lambda item: item.published_at or item.release_version)[-1]
        ready_for = ["workspace"]
        blocked_for: list[str] = []
        if best.status is DatasetStatus.APPROVED_FOR_BACKTEST:
            ready_for.append("backtest")
        else:
            blocked_for.append("backtest")
        return {
            "status": "ready_for_backtest" if "backtest" in ready_for else "ready_for_workspace",
            "ready_for": ready_for,
            "blocked_for": blocked_for,
            "issues": [],
        }

    def _live_status(self, live_views) -> dict[str, object]:
        if not live_views:
            return {
                "status": "not_configured",
                "ready_for": [],
                "blocked_for": ["shadow", "paper", "live"],
                "issues": [],
            }
        latest = live_views[-1]
        gate = evaluate_live_view_freshness(latest, policy=PAPER_LIVE_FRESHNESS_POLICY)
        diagnostics = dict(gate.channel_diagnostics)
        evidence = latest.live_data_plane.get("freshness_evidence")
        capture_enabled = bool(isinstance(evidence, dict) and int(evidence.get("event_count") or 0) > 0)
        summary = {
            "freshness_status": gate.freshness_status,
            "max_age_seconds": gate.max_age_seconds,
            "dropped": int(diagnostics.get("dropped") or 0),
            "overflow": int(diagnostics.get("overflow") or 0),
            "sequence_gaps": int(diagnostics.get("sequence_gaps") or 0),
            "capture": "enabled" if capture_enabled else "not_verified",
        }
        if gate.passed:
            return {
                "status": "ready_for_paper",
                "ready_for": ["shadow", "paper"],
                "blocked_for": ["live"],
                "issues": [],
                **summary,
            }
        if gate.freshness_status == "configured":
            issues = ["freshness_not_verified"]
        elif gate.channel_failures:
            issues = list(gate.channel_failures)
        elif gate.freshness_status not in PAPER_LIVE_FRESHNESS_POLICY.passing_statuses:
            issues = [f"freshness_{gate.freshness_status}"]
        else:
            issues = ["freshness_not_verified"]
        return {
            "status": "needs_fix",
            "ready_for": ["shadow"],
            "blocked_for": ["paper", "live"],
            "issues": issues,
            **summary,
        }

    def _live_views(self, dataset: str):
        directory = self.root / "live-views" / dataset.replace(".", "/")
        manifests = []
        for path in sorted(directory.glob("*/manifest.json")):
            manifest = load_live_view_manifest(path)
            if manifest.dataset_id == dataset:
                manifests.append(manifest)
        return manifests

    @staticmethod
    def _overall_status(historical, live, releases, live_views, issues) -> str:
        if issues and historical["status"] == "needs_fix":
            return "needs_fix"
        if live_views and live["status"] == "ready_for_paper":
            return "ready_for_paper"
        if releases and historical["status"] in {"ready_for_workspace", "ready_for_backtest"}:
            return str(historical["status"])
        if live_views:
            return str(live["status"])
        return "not_configured"

    @staticmethod
    def _source_kind(releases: tuple[DatasetRelease, ...], live_views) -> str | None:
        if releases:
            providers = {release.provider for release in releases}
            if providers <= {"user-write", None}:
                return "user_defined"
            return "built_in"
        if live_views:
            source = live_views[-1].source
            value = source.get("source_kind") if isinstance(source, dict) else None
            return str(value or "user_defined")
        return None

    @staticmethod
    def _live_primary_time(live_views) -> str | None:
        return live_views[-1].primary_time if live_views else None

    @staticmethod
    def _next_action(dataset: str, historical, live, issues: list[str]) -> str:
        if historical["status"] == "needs_fix":
            return f"reacquire {dataset}; current release needs metadata or quality repair"
        if live["status"] == "needs_fix":
            return f"kairospy data doctor {dataset} --verbose"
        if historical["status"] == "not_configured" and live["status"] == "not_configured":
            return f"kairospy data add <file-or-connector> --name {dataset}"
        if "backtest" in historical.get("blocked_for", ()):
            return f"kairospy data promote {dataset} --for backtest"
        return f"kairospy data query --dataset {dataset}"
