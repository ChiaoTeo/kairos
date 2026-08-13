from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import subprocess
import sys

from kairospy import Kairos, ResearchPeriod, ResearchSpec
from kairospy.application.data import DatasetSetRef
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli import execute_argv


def _split() -> dict[str, object]:
    return {
        "sample_count": 10,
        "missing_rate": 0.0,
        "estimate": 0.02,
        "baseline_estimate": 0.0,
        "bootstrap_ci_lower": 0.01,
        "bootstrap_ci_upper": 0.03,
        "cost_scenarios": {
            "mid_sensitivity": 0.03,
            "conservative_bid_ask": 0.02,
            "fees": 0.015,
            "stress_slippage": 0.005,
        },
    }


def _project(tmp_path: Path) -> tuple[Path, Kairos, ResearchSpec, Path, Path]:
    project = tmp_path / "research-cli"
    WorkspaceApplication().init_project(project, workspace_id="research-cli")
    kairos = Kairos.open(project)
    ref = kairos.data.catalog.publish(
        dataset_id="market.quote/SPY/research-cli",
        owner="market",
        kind="quote",
        subject="SPY-options",
        events=(
            {
                "Quote": {
                    "instrument_id": "O:SPY260116P00500000",
                    "observed_at_unix_nanos": 1,
                }
            },
        ),
        lineage={"provider": "fixture"},
        quality_report={"event_count": 1},
    )
    dataset_set = DatasetSetRef(
        (ref,),
        composition_policy={
            "time_alignment": "point-in-time",
            "conflict_policy": "reject",
        },
    )
    kairos.data.publish_trust_report(dataset_set, required_kinds=("quote",))
    spec = ResearchSpec(
        research_id="spy-put-skew-cli",
        hypothesis="SPY Put IV exceeds subsequent realized volatility",
        dataset_set=dataset_set,
        observation_rule="Wednesday 15:30 America/New_York",
        feature_availability_rule="feature.available_at <= observation_time",
        label_rule="label.start_time > observation_time",
        in_sample=ResearchPeriod("2024-01-01T00:00:00Z", "2024-07-01T00:00:00Z"),
        validation=ResearchPeriod("2024-07-01T00:00:00Z", "2025-01-01T00:00:00Z"),
        holdout=ResearchPeriod("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        baseline="unconditional future realized volatility",
        seed=42,
        code_version="fixture-1",
        quote_stale_window_nanos=60_000_000_000,
    )
    plan_path = project / "research-plan.json"
    plan_path.write_text(json.dumps(spec.as_dict()), encoding="utf-8")
    evidence_path = project / "research-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "results": {
                    "in_sample": _split(),
                    "validation": _split(),
                    "holdout": _split(),
                },
                "conclusion": "insufficient-evidence",
                "limitations": ["fixture evidence"],
            }
        ),
        encoding="utf-8",
    )
    return project, kairos, spec, plan_path, evidence_path


def test_research_cli_does_not_import_strategy_runtime(tmp_path: Path) -> None:
    project, _, _, plan_path, _ = _project(tmp_path)
    script = (
        "import sys; from io import StringIO; "
        "from kairospy.surface.cli import execute_argv; out=StringIO(); "
        f"code=execute_argv(['research','plan','lock',{str(plan_path)!r},"
        f"'--workspace',{str(project)!r},'--output','json'],out); "
        "assert code == 0, out.getvalue(); "
        "assert 'kairospy.application.strategy' not in sys.modules"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_research_cli_and_python_share_plan_and_gate_applications(
    tmp_path: Path,
) -> None:
    project, kairos, spec, plan_path, evidence_path = _project(tmp_path)

    output = StringIO()
    assert (
        execute_argv(
            [
                "research",
                "plan",
                "lock",
                str(plan_path),
                "--workspace",
                str(project),
                "--output",
                "json",
            ],
            output,
        )
        == 0
    )
    locked = json.loads(output.getvalue())
    assert locked == kairos.research.plan(spec.plan_hash)

    output = StringIO()
    assert (
        execute_argv(
            [
                "research",
                "gate",
                "publish",
                str(plan_path),
                str(evidence_path),
                "--workspace",
                str(project),
                "--output",
                "json",
            ],
            output,
        )
        == 0
    )
    report = json.loads(output.getvalue())

    output = StringIO()
    assert (
        execute_argv(
            [
                "research",
                "gate",
                "show",
                spec.plan_hash,
                "--workspace",
                str(project),
                "--output",
                "json",
            ],
            output,
        )
        == 0
    )
    assert json.loads(output.getvalue()) == report
    assert report == kairos.research.gate_report(spec.plan_hash)


def test_research_spec_document_round_trip_is_hash_stable(tmp_path: Path) -> None:
    _, _, spec, _, _ = _project(tmp_path)
    rebuilt = ResearchSpec.from_dict(spec.as_dict())
    assert rebuilt == spec
    assert rebuilt.plan_hash == spec.plan_hash
