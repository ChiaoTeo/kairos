from __future__ import annotations

import json

from typer.testing import CliRunner

from kairospy.application.system.control.registry import RunRegistry, list_run_daemons
from kairospy.surface.products.run import run_app


def test_run_registry_lists_summary_and_writes_stop_command(tmp_path) -> None:
    run_dir = tmp_path / "backtest" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({"run_id": "run-1", "mode": "backtest", "strategy_id": "s", "event_count": 3}),
        encoding="utf-8",
    )

    records = RunRegistry(tmp_path).list(mode="backtest")
    assert records[0].to_dict()["status"] == "stopped"
    assert list_run_daemons(root=tmp_path)[0].run_id == "run-1"

    command = RunRegistry(tmp_path).request_stop(mode="backtest", run_id="run-1", reason="test")
    assert json.loads(command.read_text(encoding="utf-8"))["desired_state"] == "stopped"
    assert records[0].to_dict()["result"]["event_count"] == 3


def test_run_daemon_stop_command_writes_command_file(tmp_path) -> None:
    result = CliRunner().invoke(
        run_app,
        ["daemon", "stop", "--root", str(tmp_path), "--mode", "backtest", "--run-id", "run-1"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert '"desired_state": "stopped"' in result.output
    assert (tmp_path / "backtest" / "run-1" / "command.json").exists()


def test_run_registry_uses_current_instance_for_stop_command(tmp_path) -> None:
    group = tmp_path / "live" / "run-1"
    instance = group / "instances" / "instance-1"
    instance.mkdir(parents=True)
    (group / "current.json").write_text(
        json.dumps({"run_id": "run-1", "mode": "live", "run_instance_id": "instance-1", "directory": str(instance)}),
        encoding="utf-8",
    )
    (group / "state.json").write_text(json.dumps({"run_id": "run-1", "mode": "live", "mirrored_from": str(instance)}), encoding="utf-8")
    (instance / "state.json").write_text(json.dumps({"run_id": "run-1", "mode": "live", "phase": "running"}), encoding="utf-8")

    records = RunRegistry(tmp_path).list(mode="live", run_id="run-1")
    command = RunRegistry(tmp_path).request_stop(mode="live", run_id="run-1", reason="test")

    assert len(records) == 1
    assert records[0].directory == instance
    assert command == instance / "command.json"
