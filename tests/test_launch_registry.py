from __future__ import annotations

import json

from typer.testing import CliRunner

from kairospy.application.launch.registry import LaunchRegistry, list_launch_daemons
from kairospy.surface.cli.commands.launch import launch_app


def test_launch_registry_lists_summary_and_writes_stop_command(tmp_path) -> None:
    run_dir = tmp_path / "backtest" / "launch-1"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({"launch_id": "launch-1", "mode": "backtest", "strategy_id": "s", "event_count": 3}),
        encoding="utf-8",
    )

    records = LaunchRegistry(tmp_path).list(mode="backtest")
    assert records[0].to_dict()["status"] == "stopped"
    assert list_launch_daemons(root=tmp_path)[0].launch_id == "launch-1"

    command = LaunchRegistry(tmp_path).request_stop(mode="backtest", launch_id="launch-1", reason="test")
    command_payload = json.loads(command.read_text(encoding="utf-8"))
    assert command_payload["desired_state"] == "stopped"
    assert command_payload["kind"] == "runtime.stop"
    assert (run_dir / "commands" / f"{command_payload['command_id']}.json").exists()
    assert records[0].to_dict()["result"]["event_count"] == 3


def test_launch_daemon_stop_command_writes_command_file(tmp_path) -> None:
    result = CliRunner().invoke(
        launch_app,
        ["daemon", "stop", "--root", str(tmp_path), "--mode", "backtest", "--launch-id", "launch-1"],
        catch_exceptions=False,
    )
    json_result = CliRunner().invoke(
        launch_app,
        ["daemon", "stop", "--root", str(tmp_path), "--mode", "backtest", "--launch-id", "launch-1", "--format", "json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "desired_state  stopped" in result.output
    assert json_result.exit_code == 0
    assert json.loads(json_result.output)["desired_state"] == "stopped"
    assert (tmp_path / "backtest" / "launch-1" / "command.json").exists()
    assert tuple((tmp_path / "backtest" / "launch-1" / "commands").glob("*.json"))


def test_launch_registry_uses_current_instance_for_stop_command(tmp_path) -> None:
    group = tmp_path / "live" / "launch-1"
    instance = group / "instances" / "instance-1"
    instance.mkdir(parents=True)
    (group / "current.json").write_text(
        json.dumps({"launch_id": "launch-1", "mode": "live", "launch_instance_id": "instance-1", "directory": str(instance)}),
        encoding="utf-8",
    )
    (group / "state.json").write_text(json.dumps({"launch_id": "launch-1", "mode": "live", "mirrored_from": str(instance)}), encoding="utf-8")
    (instance / "state.json").write_text(json.dumps({"launch_id": "launch-1", "mode": "live", "phase": "running"}), encoding="utf-8")

    records = LaunchRegistry(tmp_path).list(mode="live", launch_id="launch-1")
    command = LaunchRegistry(tmp_path).request_stop(mode="live", launch_id="launch-1", reason="test")

    assert len(records) == 1
    assert records[0].directory == instance
    assert command == instance / "command.json"


def test_launch_registry_rejects_command_when_current_instance_is_not_running(tmp_path) -> None:
    group = tmp_path / "system" / "kairos-system"
    instance = group / "instances" / "instance-1"
    instance.mkdir(parents=True)
    (group / "current.json").write_text(
        json.dumps({"launch_id": "kairos-system", "mode": "system", "launch_instance_id": "instance-1", "directory": str(instance)}),
        encoding="utf-8",
    )
    (group / "state.json").write_text(json.dumps({"launch_id": "kairos-system", "mode": "system", "mirrored_from": str(instance)}), encoding="utf-8")
    (instance / "state.json").write_text(json.dumps({"launch_id": "kairos-system", "mode": "system", "phase": "stopped"}), encoding="utf-8")

    result = CliRunner().invoke(
        launch_app,
        ["system", "command", "account.current", "--root", str(tmp_path), "--no-wait"],
        catch_exceptions=False,
    )

    assert result.exit_code != 0
    assert "system runtime is not running" in result.output
    assert "kairospy launch system up" in result.output
    assert not (instance / "commands").exists()
