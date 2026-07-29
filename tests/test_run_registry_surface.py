from __future__ import annotations

from io import StringIO
import json

from typer.testing import CliRunner

from kairospy.config import KairosConfig
from kairospy.surface.app import AppSession
from kairospy.surface.products.run import run_app
from kairospy.surface.state import SurfaceContext


def test_run_list_reads_rewritten_runtime_artifact_registry(tmp_path) -> None:
    directory = tmp_path / "backtest" / "bt-1"
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(
        json.dumps({"run_id": "bt-1", "mode": "backtest", "event_count": 2}) + "\n",
        encoding="utf-8",
    )
    (directory / "run.log").write_text("strategy output\n", encoding="utf-8")

    result = CliRunner().invoke(run_app, ["list", "--root", str(tmp_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["runs"][0]["run_id"] == "bt-1"
    assert payload["runs"][0]["mode"] == "backtest"
    assert payload["runs"][0]["log_file"] == str(directory / "run.log")

    text = CliRunner().invoke(run_app, ["list", "--root", str(tmp_path), "--format", "text"], catch_exceptions=False)

    assert str(directory / "run.log") in text.output


def test_run_daemon_status_uses_artifact_registry(tmp_path) -> None:
    directory = tmp_path / "backtest" / "bt-1"
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(
        json.dumps({"run_id": "bt-1", "mode": "backtest", "event_count": 2}) + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        run_app,
        ["daemon", "status", "--root", str(tmp_path), "--run-id", "bt-1", "--format", "json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["runs"][0]["run_id"] == "bt-1"


def test_app_run_workspace_prompt_has_default_run_state(tmp_path) -> None:
    session = AppSession(
        stdout=StringIO(),
        context=SurfaceContext(config=KairosConfig(source_path=None, root=tmp_path, values={})),
    )

    assert session.prompt() == "kairos/app> "
    assert session.handle("1") is False

    assert session.prompt() == "kairos/app/run> "
