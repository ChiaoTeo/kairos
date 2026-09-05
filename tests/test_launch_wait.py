from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from kairospy.system.apps.launch.application import LaunchRuntimeApplication
from kairospy.system.apps.launch.application import runtime as launch_runtime
from kairospy.system.apps.launch.application.control import LaunchControlApplication
from kairospy.system.apps.launch.application.registry import LaunchRegistryApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.surface.cli import execute_argv


def _registered_backtest(tmp_path: Path):
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="launch-wait"
    )
    registry = LaunchRegistryApplication(workspace)
    registry.add("demo", mode="backtest", instance_id="run-1")
    return workspace, registry


def _avoid_process_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        launch_runtime,
        "cleanup_instance_components",
        lambda *_args, **_kwargs: {},
    )


def test_wait_requires_a_report_before_marking_backtest_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, registry = _registered_backtest(tmp_path)
    _avoid_process_cleanup(monkeypatch)
    monkeypatch.setattr(
        LaunchControlApplication,
        "status",
        lambda *_args, **_kwargs: {
            "status": "not_running",
            "registry_state": "running",
        },
    )

    result = LaunchRuntimeApplication(workspace).wait("demo", timeout=0.1)

    assert result["status"] == "failed"
    assert result["report"] is None
    assert "without a report" in result["failure_reason"]
    assert result["next_action"] == "kairos launch logs demo"
    assert registry.instances("demo")[0]["state"] == "failed"


def test_wait_preserves_a_failed_registry_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, registry = _registered_backtest(tmp_path)
    registry.update_state("demo", mode="backtest", instance_id="run-1", state="failed")
    _avoid_process_cleanup(monkeypatch)
    monkeypatch.setattr(
        LaunchControlApplication,
        "status",
        lambda *_args, **_kwargs: {
            "status": "not_running",
            "registry_state": "failed",
        },
    )

    result = LaunchRuntimeApplication(workspace).wait("demo", timeout=0.1)

    assert result["status"] == "failed"
    assert "previously marked failed" in result["failure_reason"]
    assert registry.instances("demo")[0]["state"] == "failed"


def test_wait_preserves_a_failed_control_status_even_when_report_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, registry = _registered_backtest(tmp_path)
    report_path = workspace.instance("backtest", "demo", "run-1").state(
        "backtest", "report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"orders": 2}), encoding="utf-8")
    _avoid_process_cleanup(monkeypatch)
    monkeypatch.setattr(
        LaunchControlApplication,
        "status",
        lambda *_args, **_kwargs: {
            "status": "failed",
            "registry_state": "running",
        },
    )

    result = LaunchRuntimeApplication(workspace).wait("demo", timeout=0.1)

    assert result["status"] == "failed"
    assert result["report"] == {"orders": 2}
    assert result["failure_reason"] == "launch control reported a failed backtest"
    assert registry.instances("demo")[0]["state"] == "failed"


def test_wait_marks_an_invalid_report_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, registry = _registered_backtest(tmp_path)
    report_path = workspace.instance("backtest", "demo", "run-1").state(
        "backtest", "report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("[", encoding="utf-8")
    _avoid_process_cleanup(monkeypatch)
    monkeypatch.setattr(
        LaunchControlApplication,
        "status",
        lambda *_args, **_kwargs: {
            "status": "not_running",
            "registry_state": "running",
        },
    )

    result = LaunchRuntimeApplication(workspace).wait("demo", timeout=0.1)

    assert result["status"] == "failed"
    assert result["report"] is None
    assert "report is invalid" in result["failure_reason"]
    assert registry.instances("demo")[0]["state"] == "failed"


def test_wait_accepts_completed_control_status_with_a_valid_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, registry = _registered_backtest(tmp_path)
    instance = workspace.instance("backtest", "demo", "run-1")
    report_path = instance.state("backtest", "report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"orders": 2}), encoding="utf-8")
    _avoid_process_cleanup(monkeypatch)
    monkeypatch.setattr(
        LaunchControlApplication,
        "status",
        lambda *_args, **_kwargs: {
            "status": "completed",
            "registry_state": "running",
        },
    )

    result = LaunchRuntimeApplication(workspace).wait("demo", timeout=0.1)

    assert result["status"] == "completed"
    assert result["report"] == {"orders": 2}
    assert "failure_reason" not in result
    assert result["next_action"] == "kairos launch report demo"
    assert registry.instances("demo")[0]["state"] == "completed"


def test_cli_wait_returns_nonzero_when_backtest_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _registry = _registered_backtest(tmp_path)
    monkeypatch.setattr(
        LaunchRuntimeApplication,
        "wait",
        lambda *_args, **_kwargs: {
            "status": "failed",
            "launch_id": "demo",
            "instance_id": "run-1",
            "report": None,
            "failure_reason": "backtest finished without a report",
            "next_action": "kairos launch logs demo",
        },
    )
    output = StringIO()

    exit_code = execute_argv(
        [
            "launch",
            "wait",
            "demo",
            "--workspace",
            str(workspace.paths.root),
            "--format",
            "json",
        ],
        output,
    )

    assert exit_code == 1
    assert json.loads(output.getvalue())["status"] == "failed"
