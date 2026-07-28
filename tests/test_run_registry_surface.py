from __future__ import annotations

import json

from typer.testing import CliRunner

from kairospy.surface.products.run import run_app


def test_run_list_reads_rewritten_runtime_artifact_registry(tmp_path) -> None:
    directory = tmp_path / "backtest" / "bt-1"
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(
        json.dumps({"run_id": "bt-1", "mode": "backtest", "event_count": 2}) + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(run_app, ["list", "--root", str(tmp_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["runs"][0]["run_id"] == "bt-1"
    assert payload["runs"][0]["mode"] == "backtest"


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
