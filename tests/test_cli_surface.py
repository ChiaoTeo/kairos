from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import re
import shlex
from types import SimpleNamespace
from decimal import Decimal

import pytest
import typer

from kairospy.system.apps.launch.application import (
    LaunchControlApplication,
    LaunchInstanceTimelineApplication,
    LaunchRegistryApplication,
    LaunchRuntimeApplication,
    LaunchRuntimeError,
)
from kairospy.system.apps.launch.application import new_instance_id
from kairospy.system.apps.workspace.application import (
    InstanceWorkspace,
    WorkspaceApplication,
)
from kairospy.surface.cli import execute_argv
from kairospy.surface.cli.commands.launch.support import (
    _decorate_launch_status,
    _resolve_launch_target,
    _resolve_stop_instance,
)
from kairospy.system.apps.launch.application.runtime import (
    requires_reference_runtime as _requires_reference_runtime,
    stop_component_safely as _stop_component_safely,
)
from kairospy.system.apps.components.application import (
    ComponentProcessApplication,
    SystemRuntimeSupervisor,
)
from kairospy.system.apps.workspace_services import WorkspaceServiceApplication
from kairospy.investment.apps.account.application import AccountAdminApplication
from kairospy.contracts.reference import (
    ReferenceHealthResponse,
    ReferenceOptionCoverage,
)
from kairospy.primitives.reference import InstrumentIdRead, ReferenceSourceIdRead
from kairospy.primitives.time import GenerationRead, SequenceRead
from kairospy.system.apps.launch import StrategyProcessController
from kairospy.surface.cli.options import OutputFormat, render


def test_cli_version_is_script_friendly() -> None:
    output = StringIO()

    assert execute_argv(["version"], output) == 0
    assert output.getvalue().strip() == "kairospy 0.2.0"


def test_project_init_creates_dot_kairos_workspace(tmp_path) -> None:
    output = StringIO()

    assert (
        execute_argv(
            ["project", "init", str(tmp_path / "demo"), "--id", "demo"], output
        )
        == 0
    )
    assert (tmp_path / "demo" / ".kairos" / "kairos.toml").exists()
    assert not (tmp_path / "demo" / "workspace.toml").exists()


def test_project_init_non_interactive_requires_explicit_inputs() -> None:
    output = StringIO()

    assert execute_argv(["project", "init", "--non-interactive"], output) != 0
    assert "project directory is required" in output.getvalue()


def test_launch_control_resolves_instance_owned_socket(tmp_path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="test")
    target = LaunchControlApplication(workspace).target(
        "btc-paper", "instance-1", mode="paper"
    )

    assert target.launch_id == "btc-paper"
    assert target.instance_id == "instance-1"
    assert target.socket_path == workspace.paths.launch_socket(
        "paper", "btc-paper", "instance-1"
    )


def test_stop_resolves_mode_and_instance_from_the_only_running_entry(
    tmp_path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="stop")
    LaunchRegistryApplication(workspace).add(
        "btc-options", mode="backtest", instance_id="run-1"
    )

    def status(_self, target):
        return {"status": "ready" if target.instance_id == "run-1" else "not_running"}

    monkeypatch.setattr(LaunchControlApplication, "status", status)

    assert _resolve_stop_instance(workspace, "btc-options", None, None) == (
        "run-1",
        "backtest",
    )


def test_launch_commands_reject_multiple_instances_when_instance_is_omitted(
    tmp_path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="resolve"
    )
    registry = LaunchRegistryApplication(workspace)
    registry.add("btc-options", mode="paper", instance_id="run-1")
    registry.add("btc-options", mode="paper", instance_id="run-2")

    with pytest.raises(typer.BadParameter, match="multiple registered instances"):
        _resolve_launch_target(workspace, "btc-options", "paper", None)
    assert _resolve_launch_target(workspace, "btc-options", "paper", "run-1") == (
        "run-1",
        "paper",
    )


def test_launch_target_resolution_does_not_guess_by_recency(
    tmp_path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="recent"
    )
    registry = LaunchRegistryApplication(workspace)
    registry.add("btc-options", mode="paper", instance_id="z-old")
    registry.add("btc-options", mode="paper", instance_id="a-new")

    with pytest.raises(typer.BadParameter, match="multiple registered instances"):
        _resolve_launch_target(workspace, "btc-options", "paper", None)


def test_launch_target_resolution_honors_explicit_instance_before_active_lookup(
    tmp_path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="explicit"
    )
    registry = LaunchRegistryApplication(workspace)
    registry.add("btc-options", mode="paper", instance_id="requested")
    registry.add("btc-options", mode="paper", instance_id="active")

    monkeypatch.setattr(
        LaunchControlApplication,
        "status",
        lambda _self, target: {
            "status": "ready" if target.instance_id == "active" else "not_running"
        },
    )

    assert _resolve_launch_target(workspace, "btc-options", None, "requested") == (
        "requested",
        "paper",
    )


def test_running_instance_ignores_terminal_control_statuses(
    tmp_path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="terminal"
    )
    registry = LaunchRegistryApplication(workspace)
    registry.add("btc-options", mode="paper", instance_id="old")
    registry.add("btc-options", mode="paper", instance_id="current")

    monkeypatch.setattr(
        LaunchControlApplication,
        "status",
        lambda _self, target: {
            "status": "stopped" if target.instance_id == "old" else "ready"
        },
    )

    assert LaunchRuntimeApplication(workspace).running_instance("btc-options") == {
        **next(
            entry
            for entry in registry.instances("btc-options")
            if entry["instance_id"] == "current"
        ),
        "status": "ready",
    }


def test_running_instance_never_guesses_between_multiple_active_instances(
    tmp_path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="multiple-running"
    )
    registry = LaunchRegistryApplication(workspace)
    registry.add("btc-options", mode="paper", instance_id="run-1")
    registry.add("btc-options", mode="paper", instance_id="run-2")
    monkeypatch.setattr(
        LaunchControlApplication,
        "status",
        lambda _self, _target: {"status": "ready"},
    )

    with pytest.raises(LaunchRuntimeError, match="multiple running instances"):
        LaunchRuntimeApplication(workspace).running_instance("btc-options", "paper")


def test_launch_target_resolution_discovers_non_paper_mode(tmp_path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="mode")
    LaunchRegistryApplication(workspace).add(
        "btc", mode="backtest", instance_id="run-1"
    )

    assert _resolve_launch_target(workspace, "btc", None, None) == ("run-1", "backtest")


def test_launch_target_resolution_rejects_ambiguous_registered_instances(
    tmp_path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="ambiguous"
    )
    registry = LaunchRegistryApplication(workspace)
    registry.add("btc", mode="paper", instance_id="run-1")
    registry.add("btc", mode="backtest", instance_id="run-2")

    with pytest.raises(typer.BadParameter, match="multiple registered instances"):
        _resolve_launch_target(workspace, "btc", None, None)


def test_launch_target_resolution_rejects_unregistered_instance(tmp_path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="missing-instance"
    )

    with pytest.raises(typer.BadParameter, match="no registered instance"):
        _resolve_launch_target(workspace, "btc", None, "run-missing")


def test_launch_target_resolution_rejects_same_instance_id_across_modes(
    tmp_path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="ambiguous-mode"
    )
    registry = LaunchRegistryApplication(workspace)
    registry.add("btc", mode="paper", instance_id="run-1")
    registry.add("btc", mode="backtest", instance_id="run-1")

    with pytest.raises(typer.BadParameter, match="exists in multiple modes"):
        _resolve_launch_target(workspace, "btc", None, "run-1")


def test_launch_status_aggregates_strategy_and_component_health(
    tmp_path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="aggregate"
    )
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    instance.component_manifest().write_text(
        '{"components":{"market":{"socket":"%s"}},"accounts":{"main":{"socket_name":"account-main"}}}'
        % instance.socket("market"),
        encoding="utf-8",
    )

    def component_status(
        _self, component, *, instance_workspace=None, socket_name=None
    ):
        del instance_workspace, socket_name
        return {"component": component, "status": "ready"}

    monkeypatch.setattr(ComponentProcessApplication, "status", component_status)

    value = _decorate_launch_status(
        workspace,
        "btc",
        "run-1",
        "paper",
        {"status": "ready", "strategy_state": "running"},
    )

    assert value["launch_status"] == "healthy"
    assert value["strategy_status"] == "ready"
    assert set(value["component_status"]) == {
        "reference",
        "market",
        "risk",
        "execution",
        "account:main",
    }


def test_launch_instance_component_risk_and_capital_status_use_instance_scope(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="instance-risk-capital-status"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    instance.component_manifest().write_text(
        '{"components":{"capital":{"socket":"%s"}},"accounts":{}}'
        % instance.socket("capital"),
        encoding="utf-8",
    )

    def component_status(
        _self, component, *, instance_workspace=None, socket_name=None
    ):
        del socket_name
        return {
            "component": component,
            "status": "ready",
            "scope_root": str(instance_workspace.root)
            if instance_workspace is not None
            else "workspace",
        }

    monkeypatch.setattr(ComponentProcessApplication, "status", component_status)

    for component in ("risk", "capital"):
        output = StringIO()
        assert (
            execute_argv(
                [
                    "launch",
                    "instance",
                    "component",
                    component,
                    "status",
                    "btc",
                    "--workspace",
                    str(workspace.paths.root),
                    "--format",
                    "json",
                ],
                output,
            )
            == 0
        )
        value = json.loads(output.getvalue())
        assert value["component"] == component
        assert value["status"] == "ready"
        assert value["scope"] == "launch-instance"
        assert value["instance_id"] == "run-1"
        assert value["scope_root"] == str(instance.root)


def test_launch_instance_component_risk_mutations_use_owner_cli_scope(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.system.apps.components.application import NativeCliApplication

    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="instance-risk-mutations"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    policy_file = tmp_path / "risk-policy.json"
    authorization_file = tmp_path / "risk-authorization.json"
    policy_file.write_text('{"policy":{"policy_id":"policy-1"}}', encoding="utf-8")
    authorization_file.write_text('{"request_id":"request-1"}', encoding="utf-8")
    calls: list[tuple[str, list[str], Path]] = []

    def run(_self, component, arguments):
        calls.append((component, arguments, _self.workspace.root))
        return {"status": "ok"}

    monkeypatch.setattr(NativeCliApplication, "run", run)

    for argv, expected_arguments in (
        (
            ["pre-trade-check", "--file", str(authorization_file)],
            ["connected", "pre-trade-check", "--file", str(authorization_file)],
        ),
        (
            ["authorize-reserve", "--file", str(authorization_file)],
            ["connected", "authorize-reserve", "--file", str(authorization_file)],
        ),
        (
            [
                "release",
                "--reservation-id",
                "reservation-1",
                "--at-unix-nanos",
                "10",
            ],
            [
                "connected",
                "release",
                "--reservation-id",
                "reservation-1",
                "--at-unix-nanos",
                "10",
            ],
        ),
        (
            [
                "consume",
                "--reservation-id",
                "reservation-2",
                "--at-unix-nanos",
                "11",
            ],
            [
                "connected",
                "consume",
                "--reservation-id",
                "reservation-2",
                "--at-unix-nanos",
                "11",
            ],
        ),
        (
            ["advance-time", "--event-time-unix-nanos", "12"],
            ["connected", "advance-time", "--event-time-unix-nanos", "12"],
        ),
        (
            [
                "resize",
                "--reservation-id",
                "reservation-3",
                "--amount",
                "12.50",
                "--at-unix-nanos",
                "13",
            ],
            [
                "connected",
                "resize",
                "--reservation-id",
                "reservation-3",
                "--amount",
                "12.50",
                "--at-unix-nanos",
                "13",
            ],
        ),
        (
            [
                "open-circuit",
                "--account-id",
                "account-1",
                "--strategy-id",
                "strategy-1",
                "--exchange-id",
                "exchange-1",
                "--at-unix-nanos",
                "14",
                "--reset-at-unix-nanos",
                "20",
                "--reason",
                "manual_hold",
            ],
            [
                "connected",
                "open-circuit",
                "--at-unix-nanos",
                "14",
                "--reason",
                "manual_hold",
                "--account-id",
                "account-1",
                "--strategy-id",
                "strategy-1",
                "--exchange-id",
                "exchange-1",
                "--reset-at-unix-nanos",
                "20",
            ],
        ),
        (
            [
                "close-circuit",
                "--account-id",
                "account-1",
                "--at-unix-nanos",
                "15",
            ],
            [
                "connected",
                "close-circuit",
                "--at-unix-nanos",
                "15",
                "--account-id",
                "account-1",
            ],
        ),
        (
            ["publish-policy", "--file", str(policy_file)],
            ["connected", "publish-policy", "--file", str(policy_file)],
        ),
    ):
        output = StringIO()
        assert (
            execute_argv(
                [
                    "launch",
                    "instance",
                    "component",
                    "risk",
                    *argv,
                    "btc",
                    "--workspace",
                    str(workspace.paths.root),
                    "--format",
                    "json",
                ],
                output,
            )
            == 0
        )
        assert json.loads(output.getvalue()) == {
            "status": "ok",
            "launch_id": "btc",
            "instance_id": "run-1",
            "mode": "paper",
            "scope": "launch-instance",
        }
        assert calls[-1] == ("risk", expected_arguments, instance.root)


def test_launch_status_reports_degraded_component(tmp_path, monkeypatch) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="degraded"
    )
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    instance.component_manifest().write_text(
        '{"components":{},"accounts":{}}', encoding="utf-8"
    )

    def component_status(
        _self, component, *, instance_workspace=None, socket_name=None
    ):
        del instance_workspace, socket_name
        return {
            "component": component,
            "status": "unresponsive" if component == "execution" else "ready",
        }

    monkeypatch.setattr(ComponentProcessApplication, "status", component_status)

    value = _decorate_launch_status(
        workspace, "btc", "run-1", "paper", {"status": "ready"}
    )

    assert value["launch_status"] == "degraded"
    assert value["component_issues"] == {"execution": "unresponsive"}


def test_launch_status_cli_does_not_require_instance_id(tmp_path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="status-cli"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "status",
                "btc",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )

    value = json.loads(output.getvalue())
    assert value["instance_id"] == "run-1"
    assert value["launch_status"] == "not_running"


def test_launch_component_cleanup_reports_stop_failures_without_raising(
    monkeypatch, tmp_path
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="cleanup"
    )
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()

    def fail(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("stale control socket")

    monkeypatch.setattr(ComponentProcessApplication, "stop", fail)
    result = _stop_component_safely(
        ComponentProcessApplication(workspace),
        "execution",
        instance_workspace=instance,
    )

    assert result["status"] == "stop_failed"
    assert "stale control socket" in result["error"]


def test_launch_instance_timeline_application_reads_and_exports(tmp_path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="timeline"
    )
    LaunchRegistryApplication(workspace).add(
        "btc", mode="backtest", instance_id="run-1"
    )
    instance = workspace.instance("backtest", "btc", "run-1")
    timeline = instance.lifecycle_journal()
    timeline.parent.mkdir(parents=True, exist_ok=True)
    timeline.write_text(
        '{"sequence": 1, "kind": "started"}\n{"sequence": 2, "kind": "stopped"}\n',
        encoding="utf-8",
    )

    application = LaunchInstanceTimelineApplication(instance)
    assert application.list(limit=1) == [{"sequence": 2, "kind": "stopped"}]
    destination = tmp_path / "export.jsonl"
    assert application.export(destination) == destination
    assert destination.read_text(encoding="utf-8").count("sequence") == 2


def test_launch_instance_timeline_cli_requires_instance_identity(tmp_path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="timeline-cli"
    )
    LaunchRegistryApplication(workspace).add(
        "btc", mode="backtest", instance_id="run-1"
    )
    instance = workspace.instance("backtest", "btc", "run-1")
    timeline = instance.lifecycle_journal()
    timeline.parent.mkdir(parents=True, exist_ok=True)
    timeline.write_text('{"sequence": 1, "kind": "started"}\n', encoding="utf-8")
    destination = tmp_path / "timeline.jsonl"

    output = StringIO()
    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "timeline",
                "export",
                "btc",
                "run-1",
                "--destination",
                str(destination),
                "--workspace",
                str(workspace.paths.root),
                "--output",
                "json",
            ],
            output,
        )
        == 0
    )
    value = json.loads(output.getvalue())
    assert value["instance_id"] == "run-1"
    assert destination.read_text(encoding="utf-8") == timeline.read_text(
        encoding="utf-8"
    )


def test_cli_render_redacts_secret_fields() -> None:
    value = {
        "api_key": "key-secret",
        "nested": {"api_secret": "api-secret"},
        "status": "ready",
    }
    output = render(value, OutputFormat.JSON)
    assert "key-secret" not in output
    assert "api-secret" not in output
    assert "[REDACTED]" in output
