from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from kairospy.configuration import DEFAULT_LAKE_ROOT
from kairospy.data.bootstrap import register_default_products
from kairospy.data.catalog import DataCatalog
from kairospy.data.client import DatasetClient
from kairospy.data.contracts import DatasetStatus, QualityLevel


@dataclass(frozen=True, slots=True)
class UsEquityReadinessCheck:
    code: str
    passed: bool
    severity: str
    evidence: object
    requirement: str
    next_action: str


class UsEquityMomentumDiagnostics:
    """Local diagnostics report for the US equity momentum data package."""

    required_products = (
        "market.returns.equity.us.1d",
        "market.universe.equity.us.1d",
        "features.liquidity.equity.us.1d",
        "features.momentum.equity.us.1d",
    )

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root = Path(root)

    def report(self, *, study_id: str = "us-equity-momentum", version: str = "1.0.0") -> dict[str, object]:
        register_default_products(self.root)
        catalog = DataCatalog(self.root)
        checks = [
            self._release_set(catalog),
            self._identity_mapping(),
            self._identity_reference_evidence(catalog),
            self._corporate_action_evidence(catalog),
            self._study_snapshot(study_id, version),
            self._universe_missing_status(catalog),
            self._known_limitations_declared(catalog),
        ]
        checks.extend(self._product_release_checks(catalog))
        passed = all(item.passed or item.severity == "warning" for item in checks)
        backtest_ready = passed and all(
            item.passed for item in checks if item.severity == "error"
        )
        return {
            "schema_version": 1,
            "package": "us-equity-momentum",
            "ready_for_study": backtest_ready,
            "ready_for_backtest": backtest_ready and self._full_market_claim_ok(checks),
            "checks": [asdict(item) for item in checks],
            "summary": {
                "passed": sum(item.passed for item in checks),
                "errors": sum(not item.passed and item.severity == "error" for item in checks),
                "warnings": sum(not item.passed and item.severity == "warning" for item in checks),
            },
            "next": self._next(checks),
        }

    def _product_release_checks(self, catalog: DataCatalog) -> list[UsEquityReadinessCheck]:
        checks = []
        for logical_key in self._release_check_products(catalog):
            try:
                release = catalog.release(logical_key)
            except KeyError:
                checks.append(UsEquityReadinessCheck(
                    f"{logical_key}:release",
                    False,
                    "error",
                    "missing",
                    "Dataset Release exists for the required US equity momentum product",
                    "Build features with kairospy features build --feature-set us-equity-momentum-v1",
                ))
                continue
            checks.append(UsEquityReadinessCheck(
                f"{logical_key}:immutable_release",
                bool(release.content_hash),
                "error",
                {"release_id": release.release_id, "content_hash": release.content_hash},
                "Release has immutable content hash",
                f"Rebuild {logical_key} as a content-addressed release",
            ))
            checks.append(UsEquityReadinessCheck(
                f"{logical_key}:quality",
                (
                    release.quality_level in {QualityLevel.BACKTEST, QualityLevel.PRODUCTION}
                    if logical_key not in {
                        "reference.corporate_actions.equity.us.massive",
                        "reference.identity.equity.us.massive",
                    }
                    else release.quality_level in {QualityLevel.STUDY, QualityLevel.BACKTEST, QualityLevel.PRODUCTION}
                )
                and release.status in {
                    DatasetStatus.APPROVED_FOR_STUDY,
                    DatasetStatus.APPROVED_FOR_BACKTEST,
                    DatasetStatus.APPROVED_FOR_PRODUCTION,
                },
                "error",
                {
                    "release_id": release.release_id,
                    "quality_level": release.quality_level.value,
                    "status": release.status.value,
                },
                "Release meets its governed quality target and is approved for study use",
                f"Run kairospy data validate --release {release.release_id}",
            ))
            directory = self.root / release.relative_path
            if logical_key == "reference.corporate_actions.equity.us.massive":
                required_metadata = ("manifest", "quality", "events")
            elif logical_key == "reference.identity.equity.us.massive":
                required_metadata = ("manifest", "quality", "mappings", "instruments", "quarantine")
            else:
                required_metadata = ("manifest", "lineage", "coverage", "quality", "schema")
            checks.append(UsEquityReadinessCheck(
                f"{logical_key}:metadata",
                all((directory / f"{name}.json").exists() for name in required_metadata),
                "error",
                str(directory),
                "Release metadata includes required governance files",
                f"Rebuild or repair release metadata for {release.release_id}",
            ))
        return checks

    def _release_set(self, catalog: DataCatalog) -> UsEquityReadinessCheck:
        present = []
        missing = []
        for logical_key in self.required_products:
            try:
                release = catalog.release(logical_key)
                present.append({"logical_key": logical_key, "release_id": release.release_id})
            except KeyError:
                missing.append(logical_key)
        return UsEquityReadinessCheck(
            "required_release_set",
            not missing,
            "error",
            {"present": present, "missing": missing},
            "returns, universe, liquidity and momentum releases are all registered",
            "Run kairospy features build --feature-set us-equity-momentum-v1 after preparing OHLCV input",
        )

    def _identity_mapping(self) -> UsEquityReadinessCheck:
        manifests = sorted((self.root / "reference" / "provider=massive" / "equity_identity").glob("version=*/manifest.json"))
        if not manifests:
            return UsEquityReadinessCheck(
                "stable_identity_mapping",
                False,
                "warning",
                "missing",
                "Massive equity identity mapping exists and records ticker changes/reuse",
                "Run kairospy data build-provider-equity-identity --provider massive with active/inactive reference rows and ticker events",
            )
        latest = json.loads(manifests[-1].read_text(encoding="utf-8"))
        return UsEquityReadinessCheck(
            "stable_identity_mapping",
            int(latest.get("mapping_count", 0)) > 0 and int(latest.get("quarantine_count", 0)) == 0,
            "error",
            latest,
            "Provider symbol mappings exist and unresolved identity events are quarantined",
            "Resolve identity quarantine before approving full-market releases",
        )

    def _study_snapshot(self, study_id: str, version: str) -> UsEquityReadinessCheck:
        path = self.root / "study-workspaces" / study_id / version / "input_releases.json"
        if not path.exists():
            return UsEquityReadinessCheck(
                "study_fixed_inputs",
                False,
                "warning",
                "missing",
                "Study workspace pins all derived input releases",
                "Run kairospy study start us-equity-momentum --dataset features.momentum.equity.us.1d",
            )
        values = json.loads(path.read_text(encoding="utf-8"))
        keys = {str(item.get("logical_key")) for item in values if isinstance(item, dict)}
        expected = set(self.required_products)
        try:
            catalog = DataCatalog(self.root)
            catalog.release("reference.corporate_actions.equity.us.massive")
            expected.add("reference.corporate_actions.equity.us.massive")
        except KeyError:
            pass
        try:
            catalog = DataCatalog(self.root)
            catalog.release("reference.identity.equity.us.massive")
            expected.add("reference.identity.equity.us.massive")
        except KeyError:
            pass
        return UsEquityReadinessCheck(
            "study_fixed_inputs",
            expected <= keys,
            "error",
            values,
            "Study workspace pins returns, universe, liquidity, momentum and available reference release hashes",
            "Recreate study workspace with study start after rebuilding inputs",
        )

    def _known_limitations_declared(self, catalog: DataCatalog) -> UsEquityReadinessCheck:
        limitations = []
        for logical_key in self.required_products:
            try:
                release = catalog.release(logical_key)
            except KeyError:
                continue
            path = self.root / release.relative_path / "quality.json"
            if path.exists():
                quality = json.loads(path.read_text(encoding="utf-8"))
                limitations.extend(quality.get("known_limitations", []) if isinstance(quality, dict) else [])
        required = ("delisting", "corporate actions", "full-market")
        text = " ".join(str(item).lower() for item in limitations)
        return UsEquityReadinessCheck(
            "known_limitations_declared",
            all(item in text for item in required),
            "warning",
            limitations,
            "Known delisting, corporate action and full-market limitations are disclosed",
            "Add known limitations to derived release quality metadata and study report",
        )

    def _identity_reference_evidence(self, catalog: DataCatalog) -> UsEquityReadinessCheck:
        try:
            universe = catalog.release("market.universe.equity.us.1d")
        except KeyError:
            return UsEquityReadinessCheck(
                "identity_reference_release",
                False,
                "warning",
                "missing universe release",
                "Universe release declares the reference identity evidence used for missing-status classification",
                "Build universe with kairospy data prepare-us-equity-momentum",
            )
        lineage_path = self.root / universe.relative_path / "lineage.json"
        lineage = json.loads(lineage_path.read_text(encoding="utf-8")) if lineage_path.exists() else {}
        source = lineage.get("source") if isinstance(lineage, dict) else {}
        source = source if isinstance(source, dict) else {}
        used_hash = source.get("reference_sha256")
        record_count = int(source.get("reference_record_count", 0) or 0)
        if not used_hash:
            return UsEquityReadinessCheck(
                "identity_reference_release",
                False,
                "warning",
                {"universe_release_id": universe.release_id, "reference_sha256": used_hash},
                "Universe lineage declares identity/reference input hash",
                "Build or auto-detect Massive equity identity before preparing US equity momentum data",
            )
        try:
            identity = catalog.release("reference.identity.equity.us.massive")
        except KeyError:
            return UsEquityReadinessCheck(
                "identity_reference_release",
                False,
                "error",
                {
                    "universe_release_id": universe.release_id,
                    "reference_sha256": used_hash,
                    "reference_record_count": record_count,
                    "release": "missing",
                },
                "Identity/reference input is registered as an immutable reference release",
                "Run data build-provider-equity-identity --provider massive or rerun data prepare-us-equity-momentum with a clean identity directory",
            )
        passed = identity.content_hash == used_hash
        return UsEquityReadinessCheck(
            "identity_reference_release",
            passed,
            "error",
            {
                "universe_release_id": universe.release_id,
                "identity_release_id": identity.release_id,
                "universe_lineage_hash": used_hash,
                "identity_hash": identity.content_hash,
                "reference_record_count": record_count,
                "status": identity.status.value,
                "quality_level": identity.quality_level.value,
            },
            "Universe lineage identity/reference hash matches the registered reference release",
            "Rebuild universe and identity reference together with data prepare-us-equity-momentum",
        )

    def _corporate_action_evidence(self, catalog: DataCatalog) -> UsEquityReadinessCheck:
        try:
            returns = catalog.release("market.returns.equity.us.1d")
        except KeyError:
            return UsEquityReadinessCheck(
                "corporate_action_release",
                False,
                "warning",
                "missing returns release",
                "Returns release declares the corporate action evidence used for total returns",
                "Build returns with kairospy data prepare-us-equity-momentum",
            )
        lineage_path = self.root / returns.relative_path / "lineage.json"
        lineage = json.loads(lineage_path.read_text(encoding="utf-8")) if lineage_path.exists() else {}
        source = lineage.get("source") if isinstance(lineage, dict) else {}
        source = source if isinstance(source, dict) else {}
        used_hash = source.get("corporate_actions_sha256")
        event_count = int(source.get("corporate_action_event_count", 0) or 0)
        if not used_hash:
            return UsEquityReadinessCheck(
                "corporate_action_release",
                False,
                "warning",
                {"returns_release_id": returns.release_id, "corporate_actions_sha256": used_hash},
                "Returns lineage declares corporate action input hash",
                "Rebuild with --sync-corporate-actions or pass --corporate-actions-directory",
            )
        try:
            actions = catalog.release("reference.corporate_actions.equity.us.massive")
        except KeyError:
            return UsEquityReadinessCheck(
                "corporate_action_release",
                event_count == 0,
                "warning" if event_count == 0 else "error",
                {
                    "returns_release_id": returns.release_id,
                    "corporate_actions_sha256": used_hash,
                    "corporate_action_event_count": event_count,
                    "release": "missing",
                },
                "Corporate action input is registered as an immutable reference release",
                "Run data prepare-us-equity-momentum with --sync-corporate-actions so split/dividend inputs are governed",
            )
        passed = actions.content_hash == used_hash
        return UsEquityReadinessCheck(
            "corporate_action_release",
            passed,
            "error",
            {
                "returns_release_id": returns.release_id,
                "corporate_actions_release_id": actions.release_id,
                "returns_lineage_hash": used_hash,
                "corporate_actions_hash": actions.content_hash,
                "corporate_action_event_count": event_count,
                "status": actions.status.value,
                "quality_level": actions.quality_level.value,
            },
            "Returns lineage corporate action hash matches the registered reference release",
            "Rebuild returns and corporate actions together with data prepare-us-equity-momentum --sync-corporate-actions",
        )

    def _universe_missing_status(self, catalog: DataCatalog) -> UsEquityReadinessCheck:
        logical_key = "market.universe.equity.us.1d"
        try:
            release = catalog.release(logical_key)
        except KeyError:
            return UsEquityReadinessCheck(
                "universe_missing_status",
                False,
                "warning",
                "missing universe release",
                "Universe release exposes missing-bar status counts",
                "Build market.universe.equity.us.1d with kairospy features build --feature-set us-equity-momentum-v1",
            )
        try:
            rows = DatasetClient(self.root).load_rows(release.release_id)
        except Exception as error:
            return UsEquityReadinessCheck(
                "universe_missing_status",
                False,
                "warning",
                {"release_id": release.release_id, "error": str(error)},
                "Universe release can be loaded to summarize missing-bar statuses",
                f"Repair or rebuild universe release {release.release_id}",
            )
        reason_counts: dict[str, int] = {}
        observed = missing = critical = eligible = 0
        for row in rows:
            if row.get("eligible"):
                eligible += 1
            if row.get("critical_gap"):
                critical += 1
            if row.get("price_observation_status") == "observed":
                observed += 1
            elif row.get("price_observation_status") == "missing_bar":
                missing += 1
                reason = str(row.get("missing_reason") or "missing_reason_missing")
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        unexplained = reason_counts.get("expected_trading_session_without_bar", 0)
        return UsEquityReadinessCheck(
            "universe_missing_status",
            unexplained == 0,
            "warning",
            {
                "release_id": release.release_id,
                "rows": len(rows),
                "observed_rows": observed,
                "missing_bar_rows": missing,
                "eligible_rows": eligible,
                "critical_gap_rows": critical,
                "missing_reason_counts": dict(sorted(reason_counts.items())),
            },
            "Universe missing bars are counted and reference-classified when evidence is available",
            "Provide equity reference/coverage evidence to split expected_trading_session_without_bar into halt, delist, download failure or provider coverage gaps",
        )

    @staticmethod
    def _full_market_claim_ok(checks: list[UsEquityReadinessCheck]) -> bool:
        limitation = next((item for item in checks if item.code == "known_limitations_declared"), None)
        if limitation is None or not limitation.passed or not isinstance(limitation.evidence, list):
            return False
        text = " ".join(str(item).lower() for item in limitation.evidence)
        return "bounded" not in text and "not proven" not in text

    def _release_check_products(self, catalog: DataCatalog) -> tuple[str, ...]:
        extras = []
        try:
            catalog.release("reference.corporate_actions.equity.us.massive")
        except KeyError:
            pass
        else:
            extras.append("reference.corporate_actions.equity.us.massive")
        try:
            catalog.release("reference.identity.equity.us.massive")
        except KeyError:
            pass
        else:
            extras.append("reference.identity.equity.us.massive")
        return (*self.required_products, *extras)

    @staticmethod
    def _next(checks: list[UsEquityReadinessCheck]) -> str:
        for check in checks:
            if not check.passed and check.severity == "error":
                return check.next_action
        for check in checks:
            if not check.passed:
                return check.next_action
        return "Ready for governed study; do not claim full-market backtest readiness until bounded/full-market limitations are removed"
