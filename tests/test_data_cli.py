from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import subprocess
import sys

from kairospy import Kairos
from kairospy.application.data import DatasetSetRef
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli import execute_argv


def _project(tmp_path: Path) -> tuple[Path, Kairos, DatasetSetRef]:
    project = tmp_path / "data-cli"
    WorkspaceApplication().init_project(project, workspace_id="data-cli")
    kairos = Kairos.open(project)
    ref = kairos.data.catalog.publish(
        dataset_id="market.quote/SPY/cli",
        owner="market",
        kind="quote",
        subject="SPY",
        events=(
            {
                "Quote": {
                    "instrument_id": "SPY",
                    "observed_at_unix_nanos": 10,
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
    kairos.data.pin_set("spy-cli", dataset_set)
    kairos.data.publish_trust_report(dataset_set, required_kinds=("quote",))
    return project, kairos, dataset_set


def test_data_cli_does_not_import_strategy_runtime(tmp_path: Path) -> None:
    project, _, _ = _project(tmp_path)
    script = (
        "import sys; from io import StringIO; "
        "from kairospy.surface.cli import execute_argv; out=StringIO(); "
        f"code=execute_argv(['data','list','--workspace',{str(project)!r},"
        "'--output','json'],out); "
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


def test_data_cli_plan_execute_journal_set_and_gate_share_applications(
    tmp_path: Path,
) -> None:
    project, _, dataset_set = _project(tmp_path)
    requirements = project / "requirements.json"
    requirements.write_text(
        json.dumps([{"owner": "market", "kind": "quote", "subject": "SPY"}]),
        encoding="utf-8",
    )
    output = StringIO()
    assert execute_argv(
        [
            "data",
            "plan",
            str(requirements),
            "--workspace",
            str(project),
            "--output",
            "json",
        ],
        output,
    ) == 0
    plan = json.loads(output.getvalue())

    output = StringIO()
    assert execute_argv(
        [
            "data",
            "execute",
            str(requirements),
            "--expected-plan-hash",
            plan["plan_hash"],
            "--workspace",
            str(project),
            "--output",
            "json",
        ],
        output,
    ) == 0
    executed = json.loads(output.getvalue())
    assert executed["execution"]["status"] == "complete"

    output = StringIO()
    assert execute_argv(
        [
            "data",
            "execution",
            plan["plan_hash"],
            "--workspace",
            str(project),
            "--output",
            "json",
        ],
        output,
    ) == 0
    assert json.loads(output.getvalue())["status"] == "complete"

    requirements.write_text(
        json.dumps([{"owner": "market", "kind": "quote", "subject": "MISSING"}]),
        encoding="utf-8",
    )
    output = StringIO()
    assert execute_argv(
        [
            "data",
            "execute",
            str(requirements),
            "--expected-plan-hash",
            plan["plan_hash"],
            "--workspace",
            str(project),
        ],
        output,
    ) != 0
    assert "reviewed data plan changed" in output.getvalue()

    output = StringIO()
    assert execute_argv(
        [
            "data",
            "set",
            "show",
            "spy-cli",
            "--workspace",
            str(project),
            "--output",
            "json",
        ],
        output,
    ) == 0
    assert json.loads(output.getvalue())["composition_hash"] == dataset_set.composition_hash

    output = StringIO()
    assert execute_argv(
        [
            "data",
            "gate",
            "show",
            dataset_set.composition_hash,
            "--workspace",
            str(project),
            "--output",
            "json",
        ],
        output,
    ) == 0
    assert json.loads(output.getvalue())["status"] == "passed"
