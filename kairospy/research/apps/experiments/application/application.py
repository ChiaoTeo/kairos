"""Reproducible Research plan and architecture Gate 2 evidence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from kairospy.research.apps.data.application import (
    DataTrustGateApplication,
    DatasetCatalogApplication,
)
from kairospy.system.apps.workspace.application import Workspace
from kairospy.research.api.protocol import ResearchSpec

if TYPE_CHECKING:
    from kairospy.system.apps.launch.application.backtests import BacktestApplication
    from kairospy.research.api.experiments import (
        BacktestBatchResult,
        BacktestCase,
        BacktestCaseResult,
    )


@dataclass(frozen=True, slots=True)
class ResearchGateApplication:
    """Validate final evidence against a plan fixed before Holdout inspection."""

    def evaluate(
        self,
        spec: ResearchSpec,
        *,
        data_trust_report: Mapping[str, Any],
        results: Mapping[str, Mapping[str, Any]],
        conclusion: str,
        limitations: tuple[str, ...],
    ) -> dict[str, Any]:
        if data_trust_report.get("status") != "passed":
            raise ValueError("Research Gate requires a passed Data Trust Gate")
        if (
            data_trust_report.get("composition_hash")
            != spec.dataset_set.composition_hash
        ):
            raise ValueError(
                "Research Gate Dataset composition does not match its plan"
            )
        if set(results) != {"in_sample", "validation", "holdout"}:
            raise ValueError("Research Gate requires in_sample, validation and holdout")
        required = {
            "sample_count",
            "missing_rate",
            "estimate",
            "baseline_estimate",
            "bootstrap_ci_lower",
            "bootstrap_ci_upper",
            "cost_scenarios",
        }
        normalized_results: dict[str, dict[str, Any]] = {}
        for split in ("in_sample", "validation", "holdout"):
            value = dict(results[split])
            missing = sorted(required - set(value))
            if missing:
                raise ValueError(
                    f"Research Gate {split} is missing evidence: " + ", ".join(missing)
                )
            if int(value["sample_count"]) <= 0:
                raise ValueError(f"Research Gate {split} sample_count must be positive")
            missing_rate = float(value["missing_rate"])
            if not 0 <= missing_rate <= 1:
                raise ValueError(f"Research Gate {split} missing_rate is invalid")
            scenarios = value["cost_scenarios"]
            if not isinstance(scenarios, Mapping) or set(scenarios) != set(
                spec.cost_scenarios
            ):
                raise ValueError(
                    f"Research Gate {split} must report every fixed cost scenario"
                )
            if float(value["bootstrap_ci_lower"]) > float(value["bootstrap_ci_upper"]):
                raise ValueError(f"Research Gate {split} Bootstrap interval is invalid")
            normalized_results[split] = value
        if conclusion not in {"accepted", "rejected", "insufficient-evidence"}:
            raise ValueError("Research conclusion must be explicit")
        if not limitations or any(not value.strip() for value in limitations):
            raise ValueError("Research Gate requires explicit limitations")
        deterministic = {
            "schema_version": 1,
            "gate": "research-trust",
            "status": "passed",
            "research_plan": spec.as_dict(),
            "research_plan_hash": spec.plan_hash,
            "data_trust_composition_hash": spec.dataset_set.composition_hash,
            "results": normalized_results,
            "conclusion": conclusion,
            "limitations": list(limitations),
        }
        digest = hashlib.sha256(
            json.dumps(
                deterministic,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return {**deterministic, "deterministic_result_sha256": digest}


class ResearchApplication:
    """Project-scoped Research use cases shared by every external surface."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        backtests: BacktestApplication | None = None,
    ) -> None:
        self.workspace = workspace
        self._backtests = backtests

    async def run_backtests(
        self,
        spec: ResearchSpec,
        cases: tuple[BacktestCase, ...],
        *,
        max_concurrency: int | None = None,
    ) -> BacktestBatchResult:
        """Validate a locked experiment and invoke canonical backtests."""

        from kairospy.research.api.experiments import (
            BacktestBatchResult,
            BacktestCaseResult,
        )

        locked = self.plan(spec.plan_hash)
        expected = json.loads(json.dumps(spec.as_dict(), sort_keys=True))
        if locked.get("research_plan") != expected:
            raise ValueError("Research experiment differs from its locked plan")
        policy = spec.experiment
        concurrency = (
            policy.max_concurrency if max_concurrency is None else max_concurrency
        )
        if concurrency <= 0 or concurrency > policy.max_concurrency:
            raise ValueError("Research experiment exceeds its concurrency limit")
        if not cases or len(cases) > policy.maximum_trials:
            raise ValueError("Research experiment exceeds its trial budget")
        case_ids = [case.case_id for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Research Backtest case IDs must be unique")
        launch_ids = [case.spec.launch_id for case in cases]
        if len(launch_ids) != len(set(launch_ids)):
            raise ValueError("Research Backtest Launch IDs must be unique")
        required_parameters = set(policy.parameter_space)
        combinations: set[str] = set()
        for case in cases:
            if case.spec.data != spec.dataset_set:
                raise ValueError("Research Backtest case uses another Dataset Set")
            if case.spec.seed != spec.seed:
                raise ValueError("Research Backtest case uses another fixed seed")
            if set(case.params) != required_parameters:
                raise ValueError(
                    "Research Backtest case parameters differ from the locked space"
                )
            for name, value in case.params.items():
                if value not in policy.parameter_space[name]:
                    raise ValueError(
                        f"Research Backtest case parameter is outside plan: {name}"
                    )
            identity = json.dumps(
                dict(case.params), sort_keys=True, separators=(",", ":")
            )
            if identity in combinations:
                raise ValueError("Research Backtest cases contain duplicate parameters")
            combinations.add(identity)

        semaphore = asyncio.Semaphore(concurrency)
        backtests = self._backtests
        if backtests is None:
            from kairospy.system.apps.launch.application.backtests import (
                BacktestApplication,
            )

            backtests = BacktestApplication(self.workspace)

        async def execute(case: BacktestCase) -> BacktestCaseResult:
            async with semaphore:
                try:
                    result = await backtests.run(case.spec, timeout=case.timeout)
                    return BacktestCaseResult(
                        case_id=case.case_id,
                        params=dict(case.params),
                        status="completed",
                        result=result,
                    )
                except Exception as error:
                    return BacktestCaseResult(
                        case_id=case.case_id,
                        params=dict(case.params),
                        status="failed",
                        error_type=type(error).__name__,
                        error=str(error),
                    )

        results = await asyncio.gather(*(execute(case) for case in cases))
        return BacktestBatchResult(cases=tuple(results), max_concurrency=concurrency)

    def pin_plan(self, spec: ResearchSpec) -> Mapping[str, Any]:
        """Immutably lock a Research plan before Holdout evidence is produced."""

        catalog = self._catalog_for(spec)
        DataTrustGateApplication(catalog).report(spec.dataset_set.composition_hash)
        path = self._plan_path(spec.plan_hash)
        research_plan = json.loads(json.dumps(spec.as_dict(), sort_keys=True))
        expected = {
            "schema_version": 1,
            "status": "locked",
            "research_plan_hash": spec.plan_hash,
            "research_plan": research_plan,
        }
        if path.is_file():
            current = json.loads(path.read_text(encoding="utf-8"))
            comparable = {
                key: current.get(key)
                for key in (
                    "schema_version",
                    "status",
                    "research_plan_hash",
                    "research_plan",
                )
            }
            if comparable != expected:
                raise ValueError("locked Research plan has conflicting content")
            return current
        value = {**expected, "locked_at": datetime.now(UTC).isoformat()}
        self._write_json(path, value)
        return value

    def publish_gate(
        self,
        spec: ResearchSpec,
        *,
        results: Mapping[str, Mapping[str, Any]],
        conclusion: str,
        limitations: tuple[str, ...],
    ) -> Mapping[str, Any]:
        """Validate and persist Gate 2 evidence in the bound Project."""

        catalog = self._catalog_for(spec)
        data_gate = (
            DataTrustGateApplication(catalog)
            .report(spec.dataset_set.composition_hash)
            .as_dict()
        )
        plan_lock = self.plan(spec.plan_hash)
        expected_plan = json.loads(json.dumps(spec.as_dict(), sort_keys=True))
        if plan_lock.get("research_plan") != expected_plan:
            raise ValueError("Research plan differs from its pre-Holdout lock")
        report = ResearchGateApplication().evaluate(
            spec,
            data_trust_report=data_gate,
            results=results,
            conclusion=conclusion,
            limitations=limitations,
        )
        report["research_plan_lock"] = {
            "research_plan_hash": spec.plan_hash,
            "locked_at": plan_lock.get("locked_at"),
        }
        path = self.workspace.paths.child(
            "state", "research", "gates", f"{spec.plan_hash}.json"
        )
        self._write_json(path, report)
        return json.loads(json.dumps(report, sort_keys=True))

    def gate_report(self, research_plan_hash: str) -> Mapping[str, Any]:
        self._validate_hash(research_plan_hash)
        path = self.workspace.paths.child(
            "state", "research", "gates", f"{research_plan_hash}.json"
        )
        return self._read_json(
            path,
            missing=f"Research Gate report does not exist: {research_plan_hash}",
            invalid="Research Gate report must be an object",
        )

    def plan(self, research_plan_hash: str) -> Mapping[str, Any]:
        path = self._plan_path(research_plan_hash)
        return self._read_json(
            path,
            missing=f"locked Research plan does not exist: {research_plan_hash}",
            invalid="locked Research plan must be an object",
        )

    def _catalog_for(self, spec: ResearchSpec) -> DatasetCatalogApplication:
        catalog = DatasetCatalogApplication(self.workspace)
        for member in spec.dataset_set.members:
            if catalog.inspect(member.dataset_id, member.version) != member:
                raise ValueError(
                    f"Research Dataset does not match Project Catalog: {member.identity}"
                )
        return catalog

    def _plan_path(self, research_plan_hash: str) -> Path:
        self._validate_hash(research_plan_hash)
        return self.workspace.paths.child(
            "state", "research", "plans", f"{research_plan_hash}.json"
        )

    @staticmethod
    def _validate_hash(value: str) -> None:
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("Research plan hash is invalid")

    @staticmethod
    def _read_json(path: Path, *, missing: str, invalid: str) -> Mapping[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(missing)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError(invalid)
        return value

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)


__all__ = [
    "ResearchApplication",
    "ResearchGateApplication",
]
