from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import threading
import time

import pytest
from typer.testing import CliRunner

from kairospy.application.modes import RuntimeMode
from kairospy.application.launch.daemon import LaunchAlreadyActiveError, LaunchDaemonService
import kairospy.application.launch.facade as launch_facade
from kairospy.application.launch.facade import LaunchFacade
from kairospy.application.launch.registry import LaunchRegistry
from kairospy.infrastructure.persistence.market_data.catalog import DataStore
from kairospy.surface.cli import app
from kairospy.surface.cli.commands.launch import launch_app
from kairospy.surface.cli.commands.system import system_app


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_launch_daemon_service_launches_backtest_foreground_and_writes_state(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-launches"

    result = LaunchDaemonService(root).launch_foreground(mode="backtest", config_path=config_path)

    assert result.phase == "stopped"
    assert result.launch_instance_id
    assert result.result["event_count"] == 2
    assert (root / "backtest" / "bt-1" / "state.json").exists()
    assert (root / "backtest" / "bt-1" / "events.jsonl").exists()
    current = json.loads((root / "backtest" / "bt-1" / "current.json").read_text(encoding="utf-8"))
    assert current["launch_instance_id"] == result.launch_instance_id
    assert (root / "backtest" / "bt-1" / "instances" / result.launch_instance_id / "state.json").exists()
    assert (root / "backtest" / "bt-1" / "instances" / result.launch_instance_id / "summary.json").exists()
    assert (root / "backtest" / "bt-1" / "instances" / result.launch_instance_id / "account" / "current.json").exists()
    record = LaunchRegistry(root).list(mode="backtest", launch_id="bt-1")[0].to_dict()
    assert record["phase"] == "stopped"
    assert record["pid"]
    assert record["identity"]["pid"] == record["pid"]
    assert record["identity"]["cwd"] == str(tmp_path)
    assert record["identity"]["root"] == str(root)
    assert record["identity"]["argv"]
    assert record["heartbeat_at"] is not None
    assert record["result"]["event_count"] == 2
    assert record["context"]["config_file"] == str(config_path)


def test_launch_daemon_start_foreground_command_launches_config(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-launches"

    result = CliRunner().invoke(
        launch_app,
        ["start", "--foreground", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["phase"] == "stopped"
    assert payload["launch_instance_id"]
    assert payload["result"]["event_count"] == 2
    assert (root / "backtest" / "bt-1" / "summary.json").exists()


def test_launch_daemon_start_uses_workspace_default_text_format(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    (tmp_path / ".kairos" / "kairos.toml").write_text(
        "[project]\nname = \"test\"\n[cli]\nformat = \"text\"\n",
        encoding="utf-8",
    )
    root = tmp_path / "daemon-launches"

    result = CliRunner().invoke(
        app,
        ["launch", "start", "--foreground", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert result.output.startswith("Launch backtest:bt-1\n")
    assert "  phase     stopped\n" in result.output
    assert "  events    2\n" in result.output


def test_launch_daemon_start_format_option_overrides_workspace_default(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    (tmp_path / ".kairos" / "kairos.toml").write_text(
        "[project]\nname = \"test\"\n[cli]\nformat = \"text\"\n",
        encoding="utf-8",
    )
    root = tmp_path / "daemon-launches"

    result = CliRunner().invoke(
        app,
        [
            "launch",
            "start",
            "--foreground",
            "--root",
            str(root),
            "--mode",
            "backtest",
            "--config",
            str(config_path),
            "--format",
            "json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["phase"] == "stopped"


def test_launch_workspace_commands_explain_status_logs_artifacts_and_stop(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-launches"
    LaunchDaemonService(root).launch_foreground(mode="backtest", config_path=config_path)

    explain = CliRunner().invoke(launch_app, ["diagnose", "explain", str(config_path), "--format", "json"], catch_exceptions=False)
    status = CliRunner().invoke(launch_app, ["status", "bt-1", "--root", str(root), "--format", "json"], catch_exceptions=False)
    logs = CliRunner().invoke(launch_app, ["logs", "bt-1", "--root", str(root), "--format", "json"], catch_exceptions=False)
    artifacts = CliRunner().invoke(launch_app, ["artifacts", "bt-1", "--root", str(root), "--format", "json"], catch_exceptions=False)
    stop = CliRunner().invoke(launch_app, ["stop", "bt-1", "--mode", "backtest", "--root", str(root)], catch_exceptions=False)

    assert explain.exit_code == 0
    assert json.loads(explain.output)["launch_config"]["launch"]["id"] == "bt-1"
    assert status.exit_code == 0
    assert json.loads(status.output)["launches"][0]["launch_id"] == "bt-1"
    assert logs.exit_code == 0
    log_payload = json.loads(logs.output)
    assert log_payload["log_file"]
    assert log_payload["lines"]
    assert artifacts.exit_code == 0
    assert any(item["name"] == "summary.json" for item in json.loads(artifacts.output)["artifacts"])
    assert stop.exit_code == 0
    assert Path(json.loads(stop.output)["command_file"]).exists()


def test_launch_daemon_start_background_command_launches_config(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-launches"

    result = CliRunner().invoke(
        launch_app,
        ["start", "--background", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["phase"] == "starting"
    assert payload["launch_instance_id"]
    summary_path = root / "backtest" / "bt-1" / "summary.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not summary_path.exists():
        time.sleep(0.05)
    assert json.loads(summary_path.read_text(encoding="utf-8"))["event_count"] == 2
    records = LaunchRegistry(root).list(mode="backtest", launch_id="bt-1")
    assert len(records) == 1
    assert records[0].to_dict()["launch_instance_id"] == payload["launch_instance_id"]
    assert records[0].phase == "stopped"


def test_launch_daemon_start_background_command_accepts_registered_target(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-launches"

    register = CliRunner().invoke(launch_app, ["targets", "add", "paper-printer", str(config_path)], catch_exceptions=False)
    result = CliRunner().invoke(
        launch_app,
        ["start", "paper-printer", "--background", "--root", str(root)],
        catch_exceptions=False,
    )

    assert register.exit_code == 0
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["phase"] == "starting"
    assert payload["launch_id"] == "bt-1"
    summary_path = root / "backtest" / "bt-1" / "summary.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not summary_path.exists():
        time.sleep(0.05)
    assert json.loads(summary_path.read_text(encoding="utf-8"))["event_count"] == 2


def test_launch_daemon_start_background_only_describes_target_before_spawning(tmp_path, monkeypatch) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-launches"
    resolver = _FakeBackgroundResolver()
    popen_calls = []

    class FakePopen:
        pid = 12345

        def __init__(self, args, **kwargs) -> None:
            popen_calls.append((args, kwargs))

    monkeypatch.setattr("kairospy.application.launch.daemon.subprocess.Popen", FakePopen)

    result = LaunchDaemonService(root, target_resolver=resolver).start_background(mode="backtest", config_path=config_path)

    assert result.phase == "starting"
    assert result.launch_id == "described-launch"
    assert resolver.described == [(RuntimeMode.BACKTEST, config_path)]
    assert resolver.resolved == []
    assert popen_calls
    assert popen_calls[0][1]["env"]["KAIROS_LAUNCH_INSTANCE_ID"] == result.launch_instance_id


def test_launch_daemon_rejects_system_reserved_launch_id_for_normal_launch(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-launches"
    service = LaunchDaemonService(root)

    with pytest.raises(ValueError, match="reserved for the built-in system runtime"):
        service.launch_foreground(mode="backtest", config_path=config_path, launch_id="kairos-system")

    with pytest.raises(ValueError, match="reserved for the built-in system runtime"):
        service.start_background(mode="backtest", config_path=config_path, launch_id="kairos-system")


def test_launch_system_rejects_non_builtin_launch_id(tmp_path) -> None:
    service = LaunchDaemonService(tmp_path / "daemon-launches")

    with pytest.raises(ValueError, match="system launch id is fixed"):
        service.launch_system_foreground(launch_id="custom-system")

    with pytest.raises(ValueError, match="system launch id is fixed"):
        service.start_system_background(launch_id="custom-system")


def test_launch_system_cli_rejects_non_builtin_launch_id(tmp_path) -> None:
    result = CliRunner().invoke(
        system_app,
        ["up", "--root", str(tmp_path / "daemon-launches"), "--launch-id", "custom-system"],
    )

    assert result.exit_code != 0
    assert "system launch id is fixed" in result.output


def test_launch_system_restart_starts_system_when_not_running(tmp_path, monkeypatch) -> None:
    root = tmp_path / "daemon-launches"
    popen_calls = []

    class FakePopen:
        pid = 12345

        def __init__(self, args, **kwargs) -> None:
            popen_calls.append((args, kwargs))

    monkeypatch.setattr("kairospy.application.launch.daemon.subprocess.Popen", FakePopen)

    result = CliRunner().invoke(
        system_app,
        ["restart", "--root", str(root), "--format", "json"],
        catch_exceptions=False,
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["action"] == "restart"
    assert payload["stopped"] is None
    assert payload["started"]["mode"] == "system"
    assert payload["started"]["phase"] == "starting"
    assert popen_calls
    assert (root / "system" / "kairos-system" / "current.json").exists()


def test_launch_system_restart_stops_active_system_before_starting_new_instance(tmp_path, monkeypatch) -> None:
    root = tmp_path / "daemon-launches"
    group = root / "system" / "kairos-system"
    instance = group / "instances" / "old-system"
    instance.mkdir(parents=True)
    (group / "current.json").write_text(
        json.dumps({"launch_id": "kairos-system", "mode": "system", "launch_instance_id": "old-system", "directory": str(instance)}),
        encoding="utf-8",
    )
    (group / "state.json").write_text(json.dumps({"launch_id": "kairos-system", "mode": "system", "mirrored_from": str(instance)}), encoding="utf-8")
    (instance / "state.json").write_text(
        json.dumps(
            {
                "launch_id": "kairos-system",
                "mode": "system",
                "launch_instance_id": "old-system",
                "phase": "running",
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    popen_calls = []

    class FakePopen:
        pid = 12345

        def __init__(self, args, **kwargs) -> None:
            popen_calls.append((args, kwargs))

    def stop_old_instance() -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not (instance / "command.json").exists():
            time.sleep(0.01)
        state = json.loads((instance / "state.json").read_text(encoding="utf-8"))
        stopped = state | {
            "phase": "stopped",
            "status": "stopped",
            "desired_state": "stopped",
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        }
        (instance / "state.json").write_text(json.dumps(stopped), encoding="utf-8")

    monkeypatch.setattr("kairospy.application.launch.daemon.subprocess.Popen", FakePopen)
    stopper = threading.Thread(target=stop_old_instance)
    stopper.start()

    result = CliRunner().invoke(
        system_app,
        ["restart", "--root", str(root), "--timeout", "2", "--format", "json"],
        catch_exceptions=False,
    )
    stopper.join(timeout=2)

    payload = json.loads(result.output)
    current = json.loads((group / "current.json").read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert payload["stopped"]["desired_state"] == "stopped"
    assert payload["started"]["launch_instance_id"] != "old-system"
    assert current["launch_instance_id"] == payload["started"]["launch_instance_id"]
    assert popen_calls


def test_launch_system_restart_rejects_non_builtin_launch_id(tmp_path) -> None:
    result = CliRunner().invoke(
        system_app,
        ["restart", "--root", str(tmp_path / "daemon-launches"), "--launch-id", "custom-system"],
    )

    assert result.exit_code != 0
    assert "system launch id is fixed" in result.output


def test_launch_daemon_rejects_second_active_instance_for_same_launch_id(tmp_path, monkeypatch) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-launches"

    class FakePopen:
        pid = 12345

        def __init__(self, args, **kwargs) -> None:
            pass

    monkeypatch.setattr("kairospy.application.launch.daemon.subprocess.Popen", FakePopen)

    service = LaunchDaemonService(root)
    first = service.start_background(mode="backtest", config_path=config_path)

    with pytest.raises(LaunchAlreadyActiveError):
        service.start_background(mode="backtest", config_path=config_path)

    current = json.loads((root / "backtest" / "bt-1" / "current.json").read_text(encoding="utf-8"))
    assert current["launch_instance_id"] == first.launch_instance_id


def test_system_inspect_reports_current_pid_and_health(tmp_path, monkeypatch) -> None:
    root = tmp_path / "daemon-launches"
    group = root / "system" / "kairos-system"
    instance = group / "instances" / "system-1"
    instance.mkdir(parents=True)
    state = {
        "launch_id": "kairos-system",
        "mode": "system",
        "launch_instance_id": "system-1",
        "phase": "running",
        "status": "running",
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "identity": {"pid": 12345, "process_id": "system-1"},
    }
    (group / "current.json").write_text(
        json.dumps({"launch_id": "kairos-system", "mode": "system", "launch_instance_id": "system-1", "directory": str(instance)}),
        encoding="utf-8",
    )
    (instance / "state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(launch_facade, "_pid_alive", lambda pid: pid == 12345)
    monkeypatch.setattr(
        launch_facade,
        "_matching_system_processes",
        lambda *, launch_root, launch_id: ({"pid": 12345, "command": "python -m kairospy system up", "argv": []},),
    )

    payload = LaunchFacade().system_inspect(root=root)

    assert payload["health"] == "healthy"
    assert payload["pid"] == 12345
    assert payload["pid_alive"] is True
    assert payload["processes"]["managed"][0]["pid"] == 12345


def test_system_health_classifies_dead_orphaned_and_conflicted() -> None:
    stale_record = {"phase": "running", "stale": True}

    assert (
        launch_facade._system_health(
            current_record=stale_record,
            pid=12345,
            pid_alive=False,
            processes=(),
            orphaned_processes=(),
        )
        == "dead"
    )
    assert (
        launch_facade._system_health(
            current_record=None,
            pid=None,
            pid_alive=None,
            processes=({"pid": 111},),
            orphaned_processes=({"pid": 111},),
        )
        == "orphaned"
    )
    assert (
        launch_facade._system_health(
            current_record={"phase": "running", "stale": False},
            pid=12345,
            pid_alive=True,
            processes=({"pid": 12345}, {"pid": 22222}),
            orphaned_processes=({"pid": 22222},),
        )
        == "conflicted"
    )


def test_system_process_match_requires_foreground_root_and_launch_id(tmp_path) -> None:
    argv = [
        "python",
        "-m",
        "kairospy",
        "system",
        "up",
        "--foreground",
        "--root",
        str(tmp_path / "launches"),
        "--launch-id",
        "kairos-system",
    ]

    assert launch_facade._matches_system_process(argv, command=" ".join(argv), launch_root=str(tmp_path / "launches"), launch_id="kairos-system")
    assert not launch_facade._matches_system_process(argv, command=" ".join(argv), launch_root=str(tmp_path / "other"), launch_id="kairos-system")
    assert not launch_facade._matches_system_process(argv, command=" ".join(argv), launch_root=str(tmp_path / "launches"), launch_id="other-system")
    assert not launch_facade._matches_system_process(
        [part for part in argv if part != "--foreground"],
        command=" ".join(part for part in argv if part != "--foreground"),
        launch_root=str(tmp_path / "launches"),
        launch_id="kairos-system",
    )


def test_system_restart_clean_stale_terminates_only_verified_current_pid(tmp_path, monkeypatch) -> None:
    root = tmp_path / "daemon-launches"
    group = root / "system" / "kairos-system"
    instance = group / "instances" / "system-1"
    instance.mkdir(parents=True)
    old = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    state = {
        "launch_id": "kairos-system",
        "mode": "system",
        "launch_instance_id": "system-1",
        "phase": "running",
        "status": "running",
        "heartbeat_at": old,
        "identity": {"pid": 12345, "process_id": "system-1"},
    }
    (group / "current.json").write_text(
        json.dumps({"launch_id": "kairos-system", "mode": "system", "launch_instance_id": "system-1", "directory": str(instance)}),
        encoding="utf-8",
    )
    (instance / "state.json").write_text(json.dumps(state), encoding="utf-8")
    killed: list[tuple[int, int]] = []
    alive = {12345: True}

    class FakePopen:
        pid = 67890

        def __init__(self, args, **kwargs) -> None:
            pass

    def fake_kill(pid: int, sig: int) -> None:
        killed.append((pid, sig))
        alive[pid] = False

    monkeypatch.setattr("kairospy.application.launch.daemon.subprocess.Popen", FakePopen)
    monkeypatch.setattr(launch_facade, "_pid_alive", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(launch_facade.os, "kill", fake_kill)
    monkeypatch.setattr(
        launch_facade,
        "_matching_system_processes",
        lambda *, launch_root, launch_id: ({"pid": 12345, "command": "python -m kairospy system up", "argv": []},),
    )

    payload = LaunchFacade().system_restart(root=root, clean_stale=True, timeout_seconds=1)

    assert killed == [(12345, launch_facade.signal.SIGTERM)]
    assert payload["cleaned"]["pid"] == 12345
    assert payload["started"]["launch_instance_id"] != "system-1"


def test_launch_daemon_start_cli_reports_active_instance_conflict(tmp_path, monkeypatch) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-launches"

    class FakePopen:
        pid = 12345

        def __init__(self, args, **kwargs) -> None:
            pass

    monkeypatch.setattr("kairospy.application.launch.daemon.subprocess.Popen", FakePopen)

    first = CliRunner().invoke(
        launch_app,
        ["start", "--background", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )
    second = CliRunner().invoke(
        launch_app,
        ["start", "--background", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert first.exit_code == 0
    assert second.exit_code != 0
    assert "launch already has an active instance" in second.output


def test_launch_daemon_allows_new_instance_after_stopped_and_keeps_artifacts_isolated(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-launches"
    service = LaunchDaemonService(root)

    first = service.launch_foreground(mode="backtest", config_path=config_path)
    second = service.launch_foreground(mode="backtest", config_path=config_path)

    assert first.launch_instance_id != second.launch_instance_id
    first_directory = root / "backtest" / "bt-1" / "instances" / str(first.launch_instance_id)
    second_directory = root / "backtest" / "bt-1" / "instances" / str(second.launch_instance_id)
    assert json.loads((first_directory / "summary.json").read_text(encoding="utf-8"))["event_count"] == 2
    assert json.loads((second_directory / "summary.json").read_text(encoding="utf-8"))["event_count"] == 2
    assert (first_directory / "account" / "current.json").exists()
    assert (second_directory / "account" / "current.json").exists()
    current = json.loads((root / "backtest" / "bt-1" / "current.json").read_text(encoding="utf-8"))
    assert current["launch_instance_id"] == second.launch_instance_id


def test_launch_daemon_heartbeat_keeps_long_running_instance_active(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "launch.toml"
    config_path.write_text("", encoding="utf-8")
    root = tmp_path / "daemon-launches"
    runner_can_finish = threading.Event()
    resolver = _FakeLongRunningResolver(runner_can_finish)
    monkeypatch.setattr("kairospy.application.launch.daemon._STALE_AFTER_SECONDS", 0.25)
    monkeypatch.setattr("kairospy.application.launch.daemon._HEARTBEAT_INTERVAL_SECONDS", 0.05)

    service = LaunchDaemonService(root, target_resolver=resolver)
    worker_error = []

    def run_worker() -> None:
        try:
            service.launch_foreground(mode="backtest", config_path=config_path)
        except Exception as error:
            worker_error.append(error)

    worker = threading.Thread(target=run_worker)
    worker.start()
    state_path = root / "backtest" / "long-launch" / "state.json"
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        if state.get("phase") == "running":
            break
        time.sleep(0.01)

    time.sleep(0.4)
    with pytest.raises(LaunchAlreadyActiveError):
        service.start_background(mode="backtest", config_path=config_path)

    runner_can_finish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert worker_error == []


def test_launch_daemon_processes_runtime_stop_command_queue(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "launch.toml"
    config_path.write_text("", encoding="utf-8")
    root = tmp_path / "daemon-launches"
    runner_can_finish = threading.Event()
    resolver = _FakeLongRunningResolver(runner_can_finish)
    monkeypatch.setattr("kairospy.application.launch.daemon._HEARTBEAT_INTERVAL_SECONDS", 0.05)

    service = LaunchDaemonService(root, target_resolver=resolver)
    worker_error = []

    def run_worker() -> None:
        try:
            service.launch_foreground(mode="backtest", config_path=config_path)
        except Exception as error:
            worker_error.append(error)

    worker = threading.Thread(target=run_worker)
    worker.start()
    instance_dir = _wait_for_running_instance(root / "backtest" / "long-launch")

    submitted = LaunchRegistry(root).submit_command(
        mode="backtest",
        launch_id="long-launch",
        kind="runtime.stop",
        payload={"reason": "test command"},
    )
    response_path = Path(str(submitted["response_file"]))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not response_path.exists():
        time.sleep(0.01)

    command_payload = json.loads((instance_dir / "command.json").read_text(encoding="utf-8"))
    response_payload = json.loads(response_path.read_text(encoding="utf-8"))
    assert command_payload["desired_state"] == "stopped"
    assert command_payload["kind"] == "runtime.stop"
    assert response_payload["status"] == "accepted"
    assert response_payload["result"]["reason"] == "test command"

    runner_can_finish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert worker_error == []


def test_launch_daemon_processes_account_query_command_queue(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "launch.toml"
    config_path.write_text("", encoding="utf-8")
    root = tmp_path / "daemon-launches"
    runner_can_finish = threading.Event()
    resolver = _FakeLongRunningResolver(runner_can_finish)
    monkeypatch.setattr("kairospy.application.launch.daemon._HEARTBEAT_INTERVAL_SECONDS", 0.05)

    service = LaunchDaemonService(root, target_resolver=resolver)
    worker_error = []

    def run_worker() -> None:
        try:
            service.launch_foreground(mode="backtest", config_path=config_path)
        except Exception as error:
            worker_error.append(error)

    worker = threading.Thread(target=run_worker)
    worker.start()
    instance_dir = _wait_for_running_instance(root / "backtest" / "long-launch")
    _write_account_current_artifact(instance_dir)

    submitted = LaunchRegistry(root).submit_command(
        mode="backtest",
        launch_id="long-launch",
        kind="account.current",
        payload={"account": "main"},
    )
    response_path = Path(str(submitted["response_file"]))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not response_path.exists():
        time.sleep(0.01)

    response_payload = json.loads(response_path.read_text(encoding="utf-8"))
    assert response_payload["status"] == "accepted"
    assert response_payload["result"]["current"]["cash"] == "1000"

    runner_can_finish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert worker_error == []


def test_launch_daemon_command_facade_enqueues_system_command(tmp_path) -> None:
    _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-launches"
    group = root / "backtest" / "bt-1"
    instance = group / "instances" / "instance-1"
    instance.mkdir(parents=True)
    (group / "current.json").write_text(
        json.dumps({"launch_id": "bt-1", "mode": "backtest", "launch_instance_id": "instance-1", "directory": str(instance)}),
        encoding="utf-8",
    )
    (group / "state.json").write_text(json.dumps({"launch_id": "bt-1", "mode": "backtest", "mirrored_from": str(instance)}), encoding="utf-8")
    (instance / "state.json").write_text(
        json.dumps({"launch_id": "bt-1", "mode": "backtest", "phase": "running", "heartbeat_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )

    payload = LaunchFacade().submit_command(
        target=None,
        root=root,
        launch_id="bt-1",
        mode=RuntimeMode.BACKTEST,
        kind="account.current",
        payload={"account": "main"},
    )

    assert payload["kind"] == "account.current"
    command = json.loads(Path(payload["command_file"]).read_text(encoding="utf-8"))
    assert command["payload"]["account"] == "main"


def test_launch_daemon_command_facade_can_wait_for_account_query_response(tmp_path, monkeypatch) -> None:
    (tmp_path / ".kairos").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".kairos" / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")
    config_path = tmp_path / "launch.toml"
    config_path.write_text("", encoding="utf-8")
    root = tmp_path / "daemon-launches"
    runner_can_finish = threading.Event()
    resolver = _FakeLongRunningResolver(runner_can_finish)
    monkeypatch.setattr("kairospy.application.launch.daemon._HEARTBEAT_INTERVAL_SECONDS", 0.05)
    service = LaunchDaemonService(root, target_resolver=resolver)
    worker_error = []

    def run_worker() -> None:
        try:
            service.launch_foreground(mode="backtest", config_path=config_path)
        except Exception as error:
            worker_error.append(error)

    worker = threading.Thread(target=run_worker)
    worker.start()
    instance_dir = _wait_for_running_instance(root / "backtest" / "long-launch")
    _write_account_current_artifact(instance_dir)

    payload = LaunchFacade().submit_command(
        target=None,
        root=root,
        launch_id="long-launch",
        mode=RuntimeMode.BACKTEST,
        kind="account.current",
        payload={"account": "main"},
        wait=True,
        timeout_seconds=2,
    )

    assert payload["response"]["status"] == "accepted"
    assert payload["response"]["result"]["current"]["cash"] == "1000"

    runner_can_finish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert worker_error == []


def test_launch_system_foreground_processes_command_queue_and_stops(tmp_path, monkeypatch) -> None:
    root = tmp_path / "daemon-launches"
    monkeypatch.setattr("kairospy.application.launch.daemon._HEARTBEAT_INTERVAL_SECONDS", 0.05)
    service = LaunchDaemonService(root)
    worker_error = []

    def run_worker() -> None:
        try:
            service.launch_system_foreground()
        except Exception as error:
            worker_error.append(error)

    worker = threading.Thread(target=run_worker, daemon=True)
    worker.start()
    instance_dir = _wait_for_running_instance(root / "system" / "kairos-system")

    submitted = LaunchRegistry(root).submit_command(
        mode="system",
        launch_id="kairos-system",
        kind="account.current",
        payload={"account": "main"},
    )
    response_path = Path(str(submitted["response_file"]))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not response_path.exists():
        time.sleep(0.01)

    response_payload = json.loads(response_path.read_text(encoding="utf-8"))
    assert response_payload["status"] == "accepted"
    assert response_payload["result"]["account"] == "main"

    stop = LaunchRegistry(root).submit_command(
        mode="system",
        launch_id="kairos-system",
        kind="runtime.stop",
        payload={"reason": "test shutdown"},
    )
    assert stop["directory"] == str(instance_dir)
    stop_response_path = Path(str(stop["response_file"]))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not stop_response_path.exists():
        time.sleep(0.01)
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert worker_error == []
    assert json.loads(stop_response_path.read_text(encoding="utf-8"))["status"] == "accepted"
    summary = json.loads((instance_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "system"
    assert summary["strategy_id"] == "cli-strategy"


def _wait_for_running_instance(group: Path) -> Path:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        current_path = group / "current.json"
        if current_path.exists():
            current = json.loads(current_path.read_text(encoding="utf-8"))
            instance_dir = Path(str(current["directory"]))
            state_path = instance_dir / "state.json"
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("phase") == "running":
                    return instance_dir
        time.sleep(0.01)
    raise AssertionError("launch instance did not reach running phase")


def _write_account_current_artifact(directory: Path) -> None:
    account = directory / "account"
    account.mkdir(parents=True, exist_ok=True)
    (account / "current.json").write_text(
        json.dumps(
            {
                "launch_id": "long-launch",
                "mode": "backtest",
                "account_view": {
                    "cash": "1000",
                    "equity": "1000",
                    "balances": [{"currency": "USDT", "total": "1000", "free": "1000", "locked": "0"}],
                    "positions": [],
                    "open_orders": [],
                    "pending_orders": [],
                },
            }
        ),
        encoding="utf-8",
    )


class _FakeBackgroundResolver:
    def __init__(self) -> None:
        self.described = []
        self.resolved = []

    def describe(self, mode: RuntimeMode, config_path: Path):
        self.described.append((mode, config_path))
        return _FakeDescriptor("described-launch", "described/launch/dir")

    def resolve(self, mode: RuntimeMode, config_path: Path):
        self.resolved.append((mode, config_path))
        raise AssertionError("background start must not resolve runtime target")


class _FakeDescriptor:
    def __init__(self, launch_id: str, launch_directory: str) -> None:
        self.launch_id = launch_id
        self.launch_directory = launch_directory


class _FakeLongRunningResolver:
    def __init__(self, runner_can_finish: threading.Event) -> None:
        self.runner_can_finish = runner_can_finish

    def describe(self, mode: RuntimeMode, config_path: Path):
        return _FakeDescriptor("long-launch", "long/launch/dir")

    def resolve(self, mode: RuntimeMode, config_path: Path):
        return _FakeLongRunningTarget(self.runner_can_finish)


class _FakeLongRunningTarget:
    def __init__(self, runner_can_finish: threading.Event) -> None:
        self.configured = _FakeLongRunningConfigured()
        self.runner_can_finish = runner_can_finish

    @property
    def launch_id(self) -> str:
        return self.configured.launch_id

    @property
    def launch_directory(self) -> str:
        return str(self.configured.launch_directory)

    def runner(self):
        self.runner_can_finish.wait(timeout=2)
        return _FakeLaunchResult()


class _FakeLongRunningConfigured:
    launch_id = "long-launch"
    launch_directory = Path("long/launch/dir")


class _FakeLaunchResult:
    runtime = None
    fills = ()
    trades = ()
    metrics = {}


def _write_backtest_project(root: Path) -> Path:
    (root / ".kairos").mkdir(parents=True, exist_ok=True)
    (root / ".kairos" / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")
    market_id = "market:binance:spot:btc_usdt"
    instrument_id = "instrument:spot:btc:usdt"
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.strategy import StrategyBase",
            "from kairospy.core.market import Bar",
            "from kairospy.core.intent import target_position_intent",
            "from kairospy.core.reference import MarketRef",
            "",
            "class ConfiguredStrategy(StrategyBase):",
            "    strategy_id = 'configured-strategy'",
            "    def __init__(self, instrument_id, market_id):",
            "        self.instrument_id = instrument_id",
            "        self.market_id = market_id",
            "        self.market_ref = MarketRef(",
            "            market_id=market_id,",
            "            instrument_id=instrument_id,",
            "            market_key='binance_spot_btc_usdt',",
            "            venue='binance',",
            "            market='spot',",
            "            source_symbol='BTC/USDT',",
            "        )",
            "        self.entered = False",
            "    def on_start(self, context):",
            "        context.subscribe(self.market_ref, selectors=(Bar.select(interval='1m'),), identity=self.strategy_id)",
            "    def on_data(self, context, signal):",
            "        if self.entered:",
            "            return None",
            "        self.entered = True",
            "        context.intent(target_position_intent(",
            "            strategy_id=self.strategy_id,",
            "            instrument_id=self.instrument_id,",
            "            market_id=self.market_id,",
            "            target_quantity=Decimal('2'),",
            "            at=signal.time,",
            "            intent_id='intent-1',",
            "        ))",
            "        return None",
        ]),
        encoding="utf-8",
    )
    DataStore(root / ".kairos" / "data", storage_format="jsonl").write(
        "market.ohlcv.binance.spot.btc_usdt.1m",
        (
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "bar",
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/USDT",
                "market_id": market_id,
                "instrument_id": instrument_id,
                "market_key": "binance_spot_btc_usdt",
                "timeframe": "1m",
                "open": "101",
                "high": "101",
                "low": "101",
                "close": "101",
                "volume": "1",
            },
        ),
        mode="replace",
    )
    config_path = root / "launch.toml"
    config_path.write_text(
        "\n".join([
            "[launch]",
            'id = "bt-1"',
            'mode = "backtest"',
            'strategy = "strategy_mod:ConfiguredStrategy"',
            "",
            "[strategy.params]",
            f'instrument_id = "{instrument_id}"',
            f'market_id = "{market_id}"',
            "",
            "[account]",
            "cash = 1000",
            'currency = "USDT"',
            "",
            "[backtest]",
            'launches_root = "launches"',
            'storage_format = "jsonl"',
            'price_field = "close"',
            "",
            "[backtest.market]",
            'start = "2026-01-01T00:00:00+00:00"',
            'end = "2026-01-01T00:01:00+00:00"',
            'on_missing = "error"',
        ])
        + "\n",
        encoding="utf-8",
    )
    return config_path
