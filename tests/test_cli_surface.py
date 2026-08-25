from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import re
import shlex
from types import SimpleNamespace

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
from kairospy.investment.apps.account.application import AccountAdminApplication
from kairospy.system.apps.launch import StrategyProcessController
from kairospy.surface.cli.options import OutputFormat, render


def test_cli_exposes_legacy_launch_entry_shape() -> None:
    output = StringIO()

    assert execute_argv(["launch", "--help"], output) == 0
    text = output.getvalue()
    assert "start" in text
    assert "status" in text
    assert "wait" in text
    assert "report" in text
    assert "strategy" in text


def test_project_init_help_exposes_runnable_backtest_template() -> None:
    output = StringIO()

    assert execute_argv(["project", "init", "--help"], output) == 0
    assert "--template" in output.getvalue()
    assert "backtest" in output.getvalue()


def test_notifications_cli_exposes_validation_and_explicit_test() -> None:
    output = StringIO()

    assert execute_argv(["notifications", "--help"], output) == 0
    text = output.getvalue()
    for command in ("setup", "list", "attach", "validate", "test", "disable", "delete"):
        assert command in text


def test_market_business_surface_rejects_connected_component_commands() -> None:
    output = StringIO()

    assert execute_argv(["market", "status"], output) != 0
    text = output.getvalue()
    assert "connected runtime command" in text
    assert "kairos system component market" in text
    assert "kairos launch instance component market" in text


def test_system_component_status_inspects_workspace_component(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="component-status"
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "component",
                "market",
                "status",
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
    assert value["component"] == "market"
    assert value["status"] == "not_running"


def test_launch_instance_component_market_status_uses_instance_scope(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-component"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    instance.component_manifest().write_text(
        '{"components":{"market":{"socket":"%s"}},"accounts":{}}'
        % instance.socket("market"),
        encoding="utf-8",
    )
    seen: list[tuple[str, object]] = []

    def status(_self, component, **kwargs):
        seen.append((component, kwargs.get("instance_workspace")))
        return {"component": component, "status": "ready"}

    monkeypatch.setattr(ComponentProcessApplication, "status", status)
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "market",
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
    assert value["component"] == "market"
    assert value["status"] == "ready"
    assert value["scope"] == "launch-instance"
    assert value["launch_id"] == "btc"
    assert value["instance_id"] == "run-1"
    assert ("market", instance) in seen


def test_launch_instance_component_market_snapshot_uses_manifest_view_root(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-market-snapshot"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    view_root = instance.snapshot()
    instance.component_manifest().write_text(
        '{"components":{"market":{"socket":"%s","view_root":"%s"}},"accounts":{}}'
        % (instance.socket("market"), view_root),
        encoding="utf-8",
    )
    socket = instance.socket("market")
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.touch()
    seen: dict[str, object] = {}

    def run(self, component, arguments):
        seen["root"] = self.workspace.paths.root
        seen["component"] = component
        seen["arguments"] = list(arguments)
        return {"status": "view_not_found", "code": "view_not_found"}

    monkeypatch.setattr(
        "kairospy.system.apps.components.application.NativeCliApplication.run", run
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "market",
                "snapshot",
                "btc",
                "quote",
                "--market-id",
                "market:binance:spot:BTCUSDT",
                "--provider",
                "binance",
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
    assert value["scope"] == "launch-instance"
    assert value["status"] == "view_not_found"
    assert seen == {
        "root": workspace.paths.root,
        "component": "market",
        "arguments": [
            "connected",
            "snapshot",
            "--socket",
            str(socket),
            "--view-root",
            str(view_root),
            "quote",
            "--market-id",
            "market:binance:spot:BTCUSDT",
            "--provider",
            "binance",
        ],
    }


def test_launch_instance_component_execution_orders_uses_connected_owner_cli(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-execution-orders"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    seen: dict[str, object] = {}

    def run(self, component, arguments):
        seen["root"] = self.workspace.paths.root
        seen["component"] = component
        seen["arguments"] = arguments
        return {"orders": []}

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.launch.support.NativeCliApplication.run",
        run,
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "execution",
                "orders",
                "btc",
                "--instance",
                "run-1",
                "--mode",
                "paper",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
                "--account-id",
                "main",
            ],
            output,
        )
        == 0
    )

    assert json.loads(output.getvalue()) == {
        "orders": [],
        "owner": "execution",
        "launch_id": "btc",
        "instance_id": "run-1",
        "mode": "paper",
        "scope": "launch-instance",
    }
    assert seen == {
        "root": workspace.paths.root,
        "component": "execution",
        "arguments": [
            "connected",
            "--mode",
            "paper",
            "--launch-id",
            "btc",
            "--instance-id",
            "run-1",
            "orders",
            "--account-id",
            "main",
        ],
    }


def test_launch_instance_component_risk_latest_uses_connected_owner_cli(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-risk-latest"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    seen: dict[str, object] = {}

    def run(self, component, arguments):
        seen["root"] = self.workspace.paths.root
        seen["component"] = component
        seen["arguments"] = arguments
        return {"kind": "latest", "generation": 7}

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.launch.support.NativeCliApplication.run",
        run,
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "risk",
                "latest",
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

    instance = workspace.instance("paper", "btc", "run-1")
    assert json.loads(output.getvalue()) == {
        "kind": "latest",
        "generation": 7,
        "launch_id": "btc",
        "instance_id": "run-1",
        "mode": "paper",
        "scope": "launch-instance",
    }
    assert seen == {
        "root": instance.paths.root,
        "component": "risk",
        "arguments": [
            "connected",
            "latest",
            "--actor-id",
            "risk:run-1",
        ],
    }

    limits_output = StringIO()
    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "risk",
                "limits",
                "btc",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            limits_output,
        )
        == 0
    )
    assert json.loads(limits_output.getvalue()) == {
        "kind": "latest",
        "generation": 7,
        "launch_id": "btc",
        "instance_id": "run-1",
        "mode": "paper",
        "scope": "launch-instance",
    }
    assert seen == {
        "root": instance.paths.root,
        "component": "risk",
        "arguments": [
            "connected",
            "limits",
            "--actor-id",
            "risk:run-1",
        ],
    }


def test_risk_system_client_latest_returns_current_view_business_facts(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.system.apps.components.application.clients import RiskSystemClient

    class FakeCurrentView:
        def latest(self):
            return {
                "kind": "latest",
                "generation": 3,
                "limits": [{"policy": {"policy_id": "policy-1"}}],
                "active_reservations": [{"reservation_id": "reservation-1"}],
                "circuits": [{"circuit_id": "circuit-1", "status": "open"}],
                "summary": {
                    "limit_count": 1,
                    "active_reservation_count": 1,
                    "open_circuit_count": 1,
                },
            }

        def limits(self):
            return ({"policy": {"policy_id": "policy-1"}},)

        def active_reservations(self):
            return ({"reservation_id": "reservation-1"},)

        def circuits(self):
            return ({"circuit_id": "circuit-1", "status": "open"},)

    seen: dict[str, object] = {}

    def latest_view(self, *, actor_id):
        seen["actor_id"] = actor_id
        return FakeCurrentView()

    monkeypatch.setattr(RiskSystemClient, "latest_view", latest_view)

    client = RiskSystemClient(tmp_path / "risk.sock", view_root=tmp_path / "snapshots")
    assert client.latest(actor_id="risk:run-1") == {
        "kind": "latest",
        "generation": 3,
        "limits": [{"policy": {"policy_id": "policy-1"}}],
        "active_reservations": [{"reservation_id": "reservation-1"}],
        "circuits": [{"circuit_id": "circuit-1", "status": "open"}],
        "summary": {
            "limit_count": 1,
            "active_reservation_count": 1,
            "open_circuit_count": 1,
        },
    }
    assert client.latest_metadata(actor_id="risk:run-1") == client.latest(
        actor_id="risk:run-1"
    )
    assert client.latest_limits(actor_id="risk:run-1") == {
        "actor_id": "risk:run-1",
        "limits": [{"policy": {"policy_id": "policy-1"}}],
    }
    assert client.latest_reservations(actor_id="risk:run-1") == {
        "actor_id": "risk:run-1",
        "active_reservations": [{"reservation_id": "reservation-1"}],
    }
    assert client.latest_circuits(actor_id="risk:run-1") == {
        "actor_id": "risk:run-1",
        "circuits": [{"circuit_id": "circuit-1", "status": "open"}],
    }
    assert seen["actor_id"] == "risk:run-1"


def test_launch_instance_component_capital_current_uses_instance_client(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-capital-current"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    seen: dict[str, object] = {}

    class FakeCapitalClient:
        def current_metadata(self, capital_group_id):
            seen["capital_group_id"] = capital_group_id
            return {
                "kind": "current",
                "generation": 9,
                "summary": {"availability_count": 1, "alert_count": 0},
                "availabilities": [{"readiness": "ready"}],
                "alerts": [],
            }

        def current_objectives(self, capital_group_id):
            seen["objectives_group_id"] = capital_group_id
            return {
                "capital_group_id": capital_group_id,
                "objectives": [{"objective_id": "objective-1"}],
            }

        def current_routes(self, capital_group_id):
            seen["routes_group_id"] = capital_group_id
            return {
                "capital_group_id": capital_group_id,
                "routes": [{"route_id": "route-1"}],
            }

    class FakeClients:
        capital = FakeCapitalClient()

    def from_connections(connections):
        seen["connections"] = connections
        return FakeClients()

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.launch.support.resolve_instance_connections",
        lambda instance: {"instance": instance.paths.root},
    )
    monkeypatch.setattr(
        "kairospy.surface.cli.commands.launch.support.InstanceSystemClients.from_connections",
        staticmethod(from_connections),
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "capital",
                "current",
                "btc",
                "--capital-group-id",
                "group-1",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )

    instance = workspace.instance("paper", "btc", "run-1")
    assert json.loads(output.getvalue()) == {
        "kind": "current",
        "generation": 9,
        "summary": {"availability_count": 1, "alert_count": 0},
        "availabilities": [{"readiness": "ready"}],
        "alerts": [],
        "launch_id": "btc",
        "instance_id": "run-1",
        "mode": "paper",
        "scope": "launch-instance",
    }
    assert seen == {
        "connections": {"instance": instance.paths.root},
        "capital_group_id": "group-1",
    }

    objectives_output = StringIO()
    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "capital",
                "objectives",
                "btc",
                "--capital-group-id",
                "group-1",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            objectives_output,
        )
        == 0
    )
    assert json.loads(objectives_output.getvalue()) == {
        "capital_group_id": "group-1",
        "objectives": [{"objective_id": "objective-1"}],
        "launch_id": "btc",
        "instance_id": "run-1",
        "mode": "paper",
        "scope": "launch-instance",
    }
    assert seen["objectives_group_id"] == "group-1"

    routes_output = StringIO()
    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "capital",
                "routes",
                "btc",
                "--capital-group-id",
                "group-1",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            routes_output,
        )
        == 0
    )
    assert json.loads(routes_output.getvalue()) == {
        "capital_group_id": "group-1",
        "routes": [{"route_id": "route-1"}],
        "launch_id": "btc",
        "instance_id": "run-1",
        "mode": "paper",
        "scope": "launch-instance",
    }
    assert seen["routes_group_id"] == "group-1"


def test_capital_system_client_current_returns_current_view_business_facts(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.system.apps.components.application.clients import CapitalSystemClient

    class FakeCurrentView:
        def current(self):
            return {
                "capital_group_id": "group-1",
                "kind": "current",
                "generation": 4,
                "summary": {
                    "availability_count": 1,
                    "alert_count": 1,
                    "critical_alert_count": 1,
                },
                "availabilities": [{"readiness": "degraded"}],
                "alerts": [{"severity": "critical"}],
            }

        def objectives(self):
            return ({"objective_id": "objective-1"},)

        def demands(self):
            return ({"demand_id": "demand-1"},)

        def plans(self):
            return ({"plan_id": "plan-1"},)

        def routes(self):
            return ({"route_id": "route-1"},)

        def reservations(self):
            return ({"reservation_id": "reservation-1"},)

        def operations(self):
            return ({"operation_id": "operation-1"},)

    seen: dict[str, object] = {}

    def current_view(self, capital_group_id):
        seen["capital_group_id"] = capital_group_id
        return FakeCurrentView()

    monkeypatch.setattr(CapitalSystemClient, "current_view", current_view)

    client = CapitalSystemClient(
        tmp_path / "capital.sock", view_root=tmp_path / "snapshots"
    )
    assert client.current("group-1") == {
        "capital_group_id": "group-1",
        "kind": "current",
        "generation": 4,
        "summary": {
            "availability_count": 1,
            "alert_count": 1,
            "critical_alert_count": 1,
        },
        "availabilities": [{"readiness": "degraded"}],
        "alerts": [{"severity": "critical"}],
    }
    assert client.current_metadata("group-1") == client.current("group-1")
    assert client.current_objectives("group-1") == {
        "capital_group_id": "group-1",
        "objectives": [{"objective_id": "objective-1"}],
    }
    assert client.current_demands("group-1") == {
        "capital_group_id": "group-1",
        "demands": [{"demand_id": "demand-1"}],
    }
    assert client.current_plans("group-1") == {
        "capital_group_id": "group-1",
        "plans": [{"plan_id": "plan-1"}],
    }
    assert client.current_routes("group-1") == {
        "capital_group_id": "group-1",
        "routes": [{"route_id": "route-1"}],
    }
    assert client.current_reservations("group-1") == {
        "capital_group_id": "group-1",
        "reservations": [{"reservation_id": "reservation-1"}],
    }
    assert client.current_operations("group-1") == {
        "capital_group_id": "group-1",
        "operations": [{"operation_id": "operation-1"}],
    }
    assert seen["capital_group_id"] == "group-1"


def test_launch_instance_component_capital_controls_use_owner_cli_scope(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.system.apps.components.application import NativeCliApplication

    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-capital-controls"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    objective_file = tmp_path / "objective.json"
    demand_file = tmp_path / "demand.json"
    cancel_file = tmp_path / "cancel-objective.json"
    reconcile_file = tmp_path / "reconcile-plan.json"
    objective_file.write_text(
        '{"request_id":"request-objective","objective_id":"objective-file"}',
        encoding="utf-8",
    )
    demand_file.write_text(
        '{"request_id":"request-demand","demand_id":"demand-file"}',
        encoding="utf-8",
    )
    cancel_file.write_text(
        json.dumps(
            {
                "request_id": "request-cancel",
                "capital_group_id": "group-1",
                "objective_id": "objective-1",
                "expected_version": 3,
                "strategy_id": "strategy-1",
                "observed_at_unix_nanos": 10,
            }
        ),
        encoding="utf-8",
    )
    reconcile_file.write_text(
        json.dumps(
            {
                "request_id": "request-reconcile",
                "capital_group_id": "group-1",
                "plan_id": "plan-1",
                "observed_at_unix_nanos": 11,
            }
        ),
        encoding="utf-8",
    )
    seen: list[tuple[str, list[str], Path]] = []

    def run(_self, component, arguments):
        seen.append((component, arguments, _self.workspace.root))
        return {"status": "ok"}

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.launch.support.resolve_instance_connections",
        lambda instance: {"instance": instance.paths.root},
    )
    monkeypatch.setattr(NativeCliApplication, "run", run)

    for argv, expected_arguments in (
        (
            [
                "publish-funding-objective",
                "btc",
                "--file",
                str(objective_file),
            ],
            ["connected", "publish-funding-objective", "--file", str(objective_file)],
        ),
        (
            [
                "observe-demand",
                "btc",
                "--file",
                str(demand_file),
            ],
            ["connected", "observe-demand", "--file", str(demand_file)],
        ),
        (
            [
                "cancel-funding-objective",
                "btc",
                "--file",
                str(cancel_file),
            ],
            ["connected", "cancel-funding-objective", "--file", str(cancel_file)],
        ),
        (
            [
                "reconcile-plan",
                "btc",
                "--file",
                str(reconcile_file),
            ],
            ["connected", "reconcile-plan", "--file", str(reconcile_file)],
        ),
    ):
        output = StringIO()
        assert (
            execute_argv(
                [
                    "launch",
                    "instance",
                    "component",
                    "capital",
                    *argv,
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
        assert seen[-1] == ("capital", expected_arguments, instance.root)


def test_system_component_market_dependents_reads_launch_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="market-dependents"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.component_manifest().write_text(
        '{"components":{"market":{"socket":"%s"}},"accounts":{}}'
        % workspace.paths.process_socket("market"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        LaunchControlApplication,
        "status",
        lambda _self, _target: {"status": "ready"},
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "component",
                "market",
                "dependents",
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
    assert value["component"] == "market"
    assert value["scope"] == "workspace"
    assert value["dependents"] == [
        {
            "launch_id": "btc",
            "mode": "paper",
            "instance_id": "run-1",
            "status": "ready",
            "component": "market",
            "socket": str(workspace.paths.process_socket("market")),
        }
    ]


def test_system_component_market_routes_requires_running_server(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="market-routes"
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "component",
                "market",
                "routes",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        != 0
    )

    text = output.getvalue()
    assert "无法连接 workspace Market 服务" in text
    assert "system component market status" in text


def test_system_component_market_replay_control_uses_owner_cli(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.system.apps.components.application import NativeCliApplication

    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="market-replay-control"
    )
    workspace.paths.process_socket("market").touch()
    calls: list[tuple[str, list[str], Path]] = []

    def run(_self, component, arguments):
        calls.append((component, arguments, _self.workspace.paths.root))
        return {"status": "ok"}

    monkeypatch.setattr(NativeCliApplication, "run", run)

    for command in (
        "recover",
        "pause-replay",
        "resume-replay",
    ):
        output = StringIO()
        assert (
            execute_argv(
                [
                    "system",
                    "component",
                    "market",
                    command,
                    "--workspace",
                    str(workspace.paths.root),
                    "--format",
                    "json",
                ],
                output,
            )
            == 0
        )
        assert json.loads(output.getvalue()) == {"scope": "system", "status": "ok"}
        assert calls[-1][0] == "market"
        assert calls[-1][1][:2] == ["connected", command]
        assert "--socket" in calls[-1][1]
        assert "--view-root" in calls[-1][1]
        assert calls[-1][2] == workspace.paths.root

    assert [call[1][1] for call in calls] == [
        "recover",
        "pause-replay",
        "resume-replay",
    ]


def test_system_component_market_subscription_control_uses_owner_cli(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.system.apps.components.application import NativeCliApplication

    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="market-subscription-control"
    )
    workspace.paths.process_socket("market").touch()
    calls: list[tuple[str, list[str], Path]] = []

    def run(_self, component, arguments):
        calls.append((component, list(arguments), _self.workspace.paths.root))
        return {"status": "ok"}

    monkeypatch.setattr(NativeCliApplication, "run", run)

    output = StringIO()
    assert (
        execute_argv(
            [
                "system",
                "component",
                "market",
                "subscribe",
                "--workspace",
                str(workspace.paths.root),
                "--subscription-id",
                "sub-1",
                "--market-id",
                "market:binance:option:BTCUSDT",
                "--strategy-id",
                "strategy-1",
                "--instance-id",
                "instance-1",
                "--data",
                "quote",
                "--require-provider",
                "binance",
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    assert json.loads(output.getvalue()) == {"scope": "system", "status": "ok"}
    assert calls[-1][0] == "market"
    assert calls[-1][1][:2] == ["connected", "subscribe"]
    arguments = calls[-1][1]
    assert arguments[arguments.index("--subscription-id") :] == [
        "--subscription-id",
        "sub-1",
        "--market-id",
        "market:binance:option:BTCUSDT",
        "--strategy-id",
        "strategy-1",
        "--instance-id",
        "instance-1",
        "--data",
        "quote",
        "--require-provider",
        "binance",
    ]
    assert calls[-1][2] == workspace.paths.root

    output = StringIO()
    assert (
        execute_argv(
            [
                "system",
                "component",
                "market",
                "unsubscribe",
                "--workspace",
                str(workspace.paths.root),
                "--subscription-id",
                "sub-1",
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    assert json.loads(output.getvalue()) == {"scope": "system", "status": "ok"}
    assert calls[-1][0] == "market"
    assert calls[-1][1][:2] == ["connected", "unsubscribe"]
    assert calls[-1][1][-2:] == ["--subscription-id", "sub-1"]


def test_system_component_market_freshness_uses_owner_cli(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.system.apps.components.application import NativeCliApplication

    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="market-freshness"
    )
    workspace.paths.process_socket("market").touch()
    calls: list[tuple[str, list[str], Path]] = []

    def run(_self, component, arguments):
        calls.append((component, list(arguments), _self.workspace.paths.root))
        return {"status": "ready"}

    monkeypatch.setattr(NativeCliApplication, "run", run)

    output = StringIO()
    assert (
        execute_argv(
            [
                "system",
                "component",
                "market",
                "freshness",
                "--workspace",
                str(workspace.paths.root),
                "--market-id",
                "market:binance:spot:BTCUSDT",
                "--provider",
                "binance",
                "--observation",
                "quote",
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )

    assert json.loads(output.getvalue()) == {"scope": "system", "status": "ready"}
    assert calls[-1][0] == "market"
    arguments = calls[-1][1]
    assert arguments[:2] == ["connected", "freshness"]
    assert arguments[arguments.index("--market-id") :] == [
        "--market-id",
        "market:binance:spot:BTCUSDT",
        "--observation",
        "quote",
        "--provider",
        "binance",
    ]
    assert calls[-1][2] == workspace.paths.root


def test_reference_business_surface_rejects_connected_component_commands() -> None:
    output = StringIO()

    assert execute_argv(["reference", "health"], output) != 0
    text = output.getvalue()
    assert "connected runtime command" in text
    assert "kairos system component reference" in text
    assert "kairos launch instance component reference" in text

    output = StringIO()
    assert execute_argv(["reference", "status"], output) != 0
    text = output.getvalue()
    assert "connected runtime command" in text
    assert "kairos system component reference" in text


def test_reference_business_surface_rejects_runtime_option_coverage() -> None:
    output = StringIO()

    assert execute_argv(["reference", "options-coverage"], output) != 0
    text = output.getvalue()
    assert "connected runtime command" in text
    assert "kairos system component reference" in text
    assert "kairos launch instance component reference" in text


def test_reference_business_surface_rejects_catalog_mutation_shortcuts() -> None:
    output = StringIO()

    assert execute_argv(["reference", "assets", "add"], output) != 0
    text = output.getvalue()
    assert "mutates the Reference catalog" in text
    assert "not a standalone catalog query" in text

    output = StringIO()
    assert execute_argv(["reference", "catalog", "listings", "add"], output) != 0
    text = output.getvalue()
    assert "mutates the Reference catalog" in text


def test_reference_option_chain_passthrough_uses_owner_cli(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="reference-option-chain"
    )
    seen: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = '{"status":"ok"}'
        stderr = ""

    def invoke(_self, arguments):
        seen.append(list(arguments))
        return Result()

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.reference.ReferenceCliApplication.invoke",
        invoke,
    )

    output = StringIO()
    assert (
        execute_argv(
            [
                "reference",
                "option-chain",
                "--underlying",
                "instrument:equity:US:AAPL:common",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        == 0
    )

    assert seen == [
        [
            "standalone",
            "option-chain",
            "--underlying",
            "instrument:equity:US:AAPL:common",
        ]
    ]
    assert json.loads(output.getvalue()) == {"status": "ok"}


def test_risk_preview_passthrough_uses_owner_standalone_cli(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="risk-preview"
    )
    seen: list[tuple[str, list[str]]] = []

    class Result:
        returncode = 0
        stdout = '{"status":"checked"}'
        stderr = ""

    def invoke(_self, component, arguments):
        seen.append((component, list(arguments)))
        return Result()

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.risk.NativeCliApplication.invoke",
        invoke,
    )

    output = StringIO()
    assert (
        execute_argv(
            [
                "risk",
                "preview",
                "--policy-file",
                "policy.json",
                "--request-file",
                "request.json",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        == 0
    )

    assert seen == [
        (
            "risk",
            [
                "standalone",
                "preview",
                "--policy-file",
                "policy.json",
                "--request-file",
                "request.json",
            ],
        )
    ]
    assert json.loads(output.getvalue()) == {"status": "checked"}


def test_risk_business_surface_rejects_connected_runtime_commands() -> None:
    output = StringIO()

    assert (
        execute_argv(["risk", "authorize-reserve", "--file", "request.json"], output)
        != 0
    )
    text = output.getvalue()
    assert "connected Risk runtime command" in text
    assert "kairos system component risk" in text
    assert "kairos launch instance component risk" in text


def test_capital_schema_passthrough_uses_owner_standalone_cli(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="capital-schema"
    )
    seen: list[tuple[str, list[str]]] = []

    class Result:
        returncode = 0
        stdout = '{"status":"schema"}'
        stderr = ""

    def invoke(_self, component, arguments):
        seen.append((component, list(arguments)))
        return Result()

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.capital.NativeCliApplication.invoke",
        invoke,
    )

    output = StringIO()
    assert (
        execute_argv(
            [
                "capital",
                "schema",
                "funding-objective",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        == 0
    )

    assert seen == [("capital", ["standalone", "schema", "funding-objective"])]
    assert json.loads(output.getvalue()) == {"status": "schema"}

    preview_output = StringIO()
    assert (
        execute_argv(
            [
                "capital",
                "preview",
                "--kind",
                "funding-objective",
                "--file",
                "objective.json",
                "--workspace",
                str(workspace.paths.root),
            ],
            preview_output,
        )
        == 0
    )
    assert seen[-1] == (
        "capital",
        [
            "standalone",
            "preview",
            "--kind",
            "funding-objective",
            "--file",
            "objective.json",
        ],
    )


def test_capital_business_surface_exposes_standalone_transfer(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="capital-transfer"
    )
    seen: list[tuple[str, list[str]]] = []

    class Result:
        returncode = 0
        stdout = '{"status":"previewed"}'
        stderr = ""

    def invoke(_self, component, arguments):
        seen.append((component, list(arguments)))
        return Result()

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.capital.NativeCliApplication.invoke",
        invoke,
    )
    output = StringIO()
    assert (
        execute_argv(
            [
                "capital",
                "transfer",
                "preview",
                "--binding-json",
                "binding.json",
                "--amount",
                "1",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        == 0
    )
    assert seen == [
        (
            "capital",
            [
                "standalone",
                "transfer",
                "preview",
                "--binding-json",
                "binding.json",
                "--amount",
                "1",
            ],
        )
    ]
    assert json.loads(output.getvalue()) == {"status": "previewed"}


def test_capital_business_surface_rejects_connected_runtime_commands() -> None:
    output = StringIO()

    assert execute_argv(["capital", "health"], output) != 0
    assert "connected Capital runtime command" in output.getvalue()


def test_system_component_reference_option_coverage_uses_workspace_client(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="reference-options-component"
    )
    calls: list[tuple[str, object]] = []

    class ReferenceClient:
        def option_coverage(self):
            calls.append(("coverage", None))
            return {"underlyings": ["SPY"]}

        def set_option_underlying(self, underlying, enabled):
            calls.append((underlying, enabled))
            return {"underlying": underlying, "enabled": enabled}

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.system.reference._workspace_reference_client",
        lambda owner: ReferenceClient(),
    )

    output = StringIO()
    assert (
        execute_argv(
            [
                "system",
                "component",
                "reference",
                "options-coverage",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    assert json.loads(output.getvalue()) == {"underlyings": ["SPY"]}

    output = StringIO()
    assert (
        execute_argv(
            [
                "system",
                "component",
                "reference",
                "options-add",
                "--underlying",
                "QQQ",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    assert json.loads(output.getvalue()) == {"underlying": "QQQ", "enabled": True}

    output = StringIO()
    assert (
        execute_argv(
            [
                "system",
                "component",
                "reference",
                "options-remove",
                "--underlying",
                "IWM",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    assert json.loads(output.getvalue()) == {"underlying": "IWM", "enabled": False}
    assert calls == [("coverage", None), ("QQQ", True), ("IWM", False)]


def test_account_business_surface_rejects_connected_component_commands() -> None:
    output = StringIO()

    assert execute_argv(["account", "fill"], output) != 0
    text = output.getvalue()
    assert "connected runtime command" in text
    assert "kairos launch instance component account" in text


def test_account_local_query_passthrough_uses_owner_standalone_cli(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="account-local-query"
    )
    seen: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = '{"source":"local_registry"}'
        stderr = ""

    def invoke(_self, arguments):
        seen.append(list(arguments))
        return Result()

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.account.AccountCliApplication.invoke",
        invoke,
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "account",
                "--account-id",
                "paper-main",
                "standalone",
                "balances",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        == 0
    )
    assert seen == [["--account-id", "paper-main", "standalone", "balances"]]
    assert json.loads(output.getvalue()) == {"source": "local_registry"}
    assert output.getvalue().endswith("\n")


def test_order_business_surface_rejects_connected_execution_commands() -> None:
    output = StringIO()

    assert execute_argv(["order", "status"], output) != 0
    text = output.getvalue()
    assert "unsupported standalone order command" in text
    assert "kairos launch instance component execution" in text
    assert "kairos system component execution" not in text


def test_order_business_surface_rejects_removed_evidence_command() -> None:
    output = StringIO()

    assert execute_argv(["order", "audit"], output) != 0
    assert "unsupported standalone order command" in output.getvalue()


def test_order_business_surface_rejects_removed_preview_submit_command() -> None:
    output = StringIO()

    assert execute_argv(["order", "preview-submit"], output) != 0
    assert "unsupported standalone order command" in output.getvalue()


def test_order_business_surface_rejects_removed_preview_action_commands() -> None:
    for command in ("preview-cancel", "preview-replace"):
        output = StringIO()
        assert execute_argv(["order", command], output) != 0
        assert "unsupported standalone order command" in output.getvalue()


def test_order_business_surface_rejects_removed_preview_file_commands() -> None:
    for command, _file_name in (
        ("preview-submit-file", "submit-order.json"),
        ("preview-cancel-file", "cancel-order.json"),
        ("preview-replace-file", "replace-order.json"),
    ):
        output = StringIO()
        assert execute_argv(["order", command], output) != 0
        assert "unsupported standalone order command" in output.getvalue()


def test_order_business_surface_rejects_all_runtime_execution_aliases() -> None:
    output = StringIO()

    assert execute_argv(["order", "unknown-remote-orders"], output) != 0
    text = output.getvalue()
    assert "unsupported standalone order command" in text
    assert "kairos launch instance component execution" in text


def test_order_business_surface_rejects_removed_backtest_short_path() -> None:
    output = StringIO()

    assert execute_argv(["order", "backtest"], output) != 0
    text = output.getvalue()
    assert "unsupported standalone order command" in text


def test_order_business_surface_rejects_missing_link_unknown_contract() -> None:
    output = StringIO()

    assert execute_argv(["order", "link-unknown"], output) != 0
    text = output.getvalue()
    assert "unsupported standalone order command" in text


def test_order_direct_query_resolves_account_binding_before_execution(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="order-direct"
    )
    binding = {
        "account_id": "main",
        "remote_account_id": "remote-main",
        "provider": "binance",
        "environment": "testnet",
        "segment_key": "spot",
        "provider_segment": "spot",
        "trading_mode": None,
        "credential_id": "credential-main",
        "credential_role": "readonly",
        "base_url": "https://example.invalid",
        "host": "",
        "port": 0,
        "client_id": 0,
        "isolated_symbol": None,
    }
    account_calls: list[list[str]] = []
    execution_calls: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.order.AccountCliApplication.run",
        lambda _self, arguments: account_calls.append(list(arguments)) or binding,
    )

    class Result:
        returncode = 0
        stdout = '{"orders":[]}'
        stderr = ""

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.order.NativeCliApplication.invoke",
        lambda _self, component, arguments: (
            execution_calls.append((component, list(arguments))) or Result()
        ),
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "order",
                "open-orders",
                "--workspace",
                str(workspace.paths.root),
                "--account-id",
                "main",
                "--segment",
                "spot",
                "--symbol",
                "BTCUSDT",
            ],
            output,
        )
        == 0
    )
    assert account_calls == [
        [
            "standalone",
            "trading-binding",
            "--account-id",
            "main",
            "--access",
            "read",
            "--segment",
            "spot",
        ]
    ]
    component, arguments = execution_calls[0]
    assert component == "execution"
    assert arguments[0:2] == ["standalone", "--binding-json"]
    assert json.loads(arguments[2]) == binding
    assert arguments[3:] == ["open-orders", "--symbol", "BTCUSDT"]


def test_order_live_write_requires_confirmation_or_yes(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="order-live-confirmation"
    )
    binding = {
        "account_id": "main",
        "remote_account_id": "remote-main",
        "provider": "binance",
        "environment": "live",
        "segment_key": "spot",
        "provider_segment": "spot",
        "trading_mode": None,
        "credential_id": "credential-main",
        "credential_role": "trade",
        "base_url": "https://api.binance.com",
        "host": "",
        "port": 0,
        "client_id": 0,
        "isolated_symbol": None,
    }
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "kairospy.surface.cli.commands.order.AccountCliApplication.run",
        lambda _self, _arguments: binding,
    )

    class Result:
        returncode = 0
        stdout = '{"outcome":{"status":"confirmed"}}'
        stderr = ""

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.order.NativeCliApplication.invoke",
        lambda _self, _component, arguments: calls.append(list(arguments)) or Result(),
    )
    argv = [
        "order",
        "cancel",
        "--workspace",
        str(workspace.paths.root),
        "--account-id",
        "main",
        "--order-id",
        "remote-1",
    ]

    denied = StringIO()
    assert execute_argv(argv, denied) != 0
    assert "scope=direct-provider" in denied.getvalue()
    assert calls == []

    accepted = StringIO()
    assert execute_argv([*argv, "--yes"], accepted) == 0
    assert calls and "--yes" not in calls[0]
    assert "--confirm-live" in calls[0]


def test_system_component_account_is_not_a_workspace_component() -> None:
    output = StringIO()

    assert execute_argv(["system", "component", "account", "balances"], output) != 0
    text = output.getvalue()
    assert "No such command" in text
    assert "account" in text


def test_launch_instance_component_execution_status_uses_instance_scope(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-execution-component"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    instance.component_manifest().write_text(
        '{"components":{"execution":{"socket":"%s"}},"accounts":{}}'
        % instance.socket("execution"),
        encoding="utf-8",
    )
    seen: list[tuple[str, object]] = []

    def status(_self, component, **kwargs):
        seen.append((component, kwargs.get("instance_workspace")))
        return {"component": component, "status": "ready"}

    monkeypatch.setattr(ComponentProcessApplication, "status", status)
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "execution",
                "status",
                "btc",
                "--instance",
                "run-1",
                "--mode",
                "paper",
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
    assert value["component"] == "execution"
    assert value["scope"] == "launch-instance"
    assert ("execution", instance) in seen


def test_launch_instance_component_reference_health_uses_manifest_client(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-reference-component"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    instance.component_manifest().write_text(
        '{"components":{"reference":{"socket":"%s","database":"%s"}},"accounts":{}}'
        % (
            workspace.paths.process_socket("reference"),
            workspace.paths.reference_database(),
        ),
        encoding="utf-8",
    )

    class ReferenceReader:
        def health(self):
            return {"status": "ready", "generation": 3}

    class ReferenceClient:
        reader = ReferenceReader()

    class Clients:
        reference = ReferenceClient()

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.launch.support.InstanceSystemClients.from_connections",
        lambda connections: Clients(),
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "reference",
                "health",
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
    assert value["status"] == "ready"
    assert value["generation"] == 3
    assert value["scope"] == "launch-instance"
    assert value["launch_id"] == "btc"
    assert value["instance_id"] == "run-1"


def test_launch_instance_component_account_balances_uses_manifest_client(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.investment.apps.account.application import AccountSnapshot
    from kairospy.primitives.account import AccountId

    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-account-component"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    instance.component_manifest().write_text(
        '{"components":{},"accounts":{"main":{"socket":"%s","view_root":"%s"}}}'
        % (instance.socket("account-main"), instance.snapshot()),
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    class CurrentView:
        def snapshot(self, account_id):
            seen["snapshot_account_id"] = str(account_id)
            return AccountSnapshot(
                account_id=AccountId("main"),
                segments=(),
                generation=7,
                event_sequence=11,
            )

    class AccountClient:
        def current_view(self, account_id):
            seen["current_view_account_id"] = str(account_id)
            return CurrentView()

    class Clients:
        accounts = {AccountId("main"): AccountClient()}

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.launch.support.InstanceSystemClients.from_connections",
        lambda connections: Clients(),
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "account",
                "balances",
                "btc",
                "--account-id",
                "main",
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
    assert value["account_id"] == "main"
    assert value["balances"] == []
    assert value["scope"] == "launch-instance"
    assert value["launch_id"] == "btc"
    assert value["instance_id"] == "run-1"
    assert seen == {"current_view_account_id": "main", "snapshot_account_id": "main"}


def test_launch_account_balances_table_uses_balance_columns() -> None:
    from kairospy.surface.cli.commands.launch.support import (
        _render_launch_account_balances,
    )

    output = _render_launch_account_balances(
        {
            "account_id": "main",
            "balances": [
                {
                    "segment_key": "spot",
                    "asset": "USDT",
                    "total": "10000",
                    "available": "9980",
                    "reserved": "20",
                }
            ],
            "launch_id": "btc",
            "instance_id": "run-1",
            "mode": "paper",
            "scope": "launch-instance",
        }
    )

    assert "Account main · launch btc/run-1 (paper)" in output
    assert "SEGMENT" in output
    assert "ASSET" in output
    assert "RESERVED" in output
    assert "spot" in output
    assert "USDT" in output
    assert "balances" not in output


def test_launch_instance_component_account_refresh_uses_owner_cli_scope(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.system.apps.components.application import NativeCliApplication

    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-account-refresh"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    instance.component_manifest().write_text(
        '{"components":{},"accounts":{"main":{"socket":"%s","view_root":"%s"}}}'
        % (instance.socket("account-main"), instance.snapshot()),
        encoding="utf-8",
    )
    calls: list[tuple[str, list[str], Path]] = []

    def run(_self, component, arguments):
        calls.append((component, arguments, _self.workspace.paths.root))
        return {"status": "ok"}

    monkeypatch.setattr(NativeCliApplication, "run", run)

    output = StringIO()
    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "account",
                "refresh",
                "btc",
                "--account-id",
                "main",
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
        "account_id": "main",
        "launch_id": "btc",
        "instance_id": "run-1",
        "mode": "paper",
        "scope": "launch-instance",
    }
    assert calls == [
        (
            "account",
            [
                "--account-id",
                "main",
                "--launch-id",
                "btc",
                "--launch-mode",
                "paper",
                "--instance-id",
                "run-1",
                "connected",
                "refresh",
            ],
            workspace.paths.root,
        )
    ]


def test_launch_instance_component_account_open_orders_is_scoped_component_result(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.primitives.account import AccountId

    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-account-open-orders"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    instance.component_manifest().write_text(
        '{"components":{},"accounts":{"main":{"socket":"%s","view_root":"%s"}}}'
        % (instance.socket("account-main"), instance.snapshot()),
        encoding="utf-8",
    )

    seen: dict[str, object] = {}

    class ObservedOrdersView:
        def open_orders(self, account_id):
            seen["open_orders_account_id"] = str(account_id)
            return {
                "account_id": str(account_id),
                "open_orders": [{"remote_order_id": "remote-1"}],
            }

    class AccountClient:
        def observed_orders_view(self, account_id):
            seen["current_view_account_id"] = str(account_id)
            return ObservedOrdersView()

    class Clients:
        accounts = {AccountId("main"): AccountClient()}

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.launch.support.InstanceSystemClients.from_connections",
        lambda connections: Clients(),
    )

    output = StringIO()
    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "account",
                "open-orders",
                "btc",
                "--account-id",
                "main",
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
        "account_id": "main",
        "open_orders": [{"remote_order_id": "remote-1"}],
        "launch_id": "btc",
        "instance_id": "run-1",
        "mode": "paper",
        "scope": "launch-instance",
    }
    assert seen == {
        "current_view_account_id": "main",
        "open_orders_account_id": "main",
    }


def test_system_component_reference_status_inspects_workspace_component(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="reference-component-status"
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "component",
                "reference",
                "status",
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
    assert value["component"] == "reference"
    assert value["status"] == "not_running"


def test_system_component_risk_and_capital_status_are_scoped_components(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="risk-capital-component-status"
    )

    for component in ("risk", "capital"):
        output = StringIO()
        assert (
            execute_argv(
                [
                    "system",
                    "component",
                    component,
                    "status",
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
        assert value["status"] == "not_running"


def test_system_component_reference_health_requires_running_server(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="reference-component-health"
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "component",
                "reference",
                "health",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        != 0
    )
    text = output.getvalue()
    assert "target server not found" in text
    assert "system up --component reference" in text


def test_system_component_risk_mutations_use_workspace_contract(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.system.apps.components.application import NativeCliApplication

    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="risk-component-mutations"
    )
    calls: list[tuple[str, list[str], Path]] = []

    def run(_self, component, arguments):
        calls.append((component, arguments, _self.workspace.paths.root))
        return {"status": "ok"}

    monkeypatch.setattr(NativeCliApplication, "run", run)
    policy_file = tmp_path / "risk-policy.json"
    authorization_file = tmp_path / "risk-authorization.json"
    authorization_file.write_text(
        json.dumps(
            {
                "request_id": "request-1",
                "idempotency_key": "idem-1",
                "reservation_id": "reservation-0",
            }
        ),
        encoding="utf-8",
    )
    policy_file.write_text(
        json.dumps(
            {
                "policy": {
                    "policy_id": "policy-1",
                    "version": 1,
                    "scope": {"account_id": "account-1"},
                    "metric": "notional",
                    "limit": "100.00",
                    "enforcement": "reject",
                    "valid_from_unix_nanos": 1,
                    "valid_until_unix_nanos": None,
                }
            }
        ),
        encoding="utf-8",
    )

    for argv, expected_arguments in (
        (
            [
                "system",
                "component",
                "risk",
                "pre-trade-check",
                "--file",
                str(authorization_file),
            ],
            ["connected", "pre-trade-check", "--file", str(authorization_file)],
        ),
        (
            [
                "system",
                "component",
                "risk",
                "authorize-reserve",
                "--file",
                str(authorization_file),
            ],
            ["connected", "authorize-reserve", "--file", str(authorization_file)],
        ),
        (
            [
                "system",
                "component",
                "risk",
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
                "system",
                "component",
                "risk",
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
            [
                "system",
                "component",
                "risk",
                "advance-time",
                "--event-time-unix-nanos",
                "12",
            ],
            ["connected", "advance-time", "--event-time-unix-nanos", "12"],
        ),
        (
            [
                "system",
                "component",
                "risk",
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
                "system",
                "component",
                "risk",
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
                "system",
                "component",
                "risk",
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
            [
                "system",
                "component",
                "risk",
                "publish-policy",
                "--file",
                str(policy_file),
            ],
            ["connected", "publish-policy", "--file", str(policy_file)],
        ),
    ):
        output = StringIO()
        assert (
            execute_argv(
                [
                    *argv,
                    "--workspace",
                    str(workspace.paths.root),
                    "--format",
                    "json",
                ],
                output,
            )
            == 0
        )
        assert json.loads(output.getvalue()) == {"status": "ok"}
        assert calls[-1] == ("risk", expected_arguments, workspace.paths.root)


def test_system_component_capital_controls_use_workspace_contract(
    tmp_path: Path, monkeypatch
) -> None:
    from kairospy.system.apps.components.application import NativeCliApplication

    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="capital-component-controls"
    )
    calls: list[tuple[str, list[str], Path]] = []
    objective_file = tmp_path / "objective.json"
    demand_file = tmp_path / "demand.json"
    cancel_file = tmp_path / "cancel-objective.json"
    reconcile_file = tmp_path / "reconcile-plan.json"
    objective_file.write_text(
        json.dumps(
            {
                "request_id": "request-objective",
                "capital_group_id": "group-1",
                "objective_id": "objective-file",
            }
        ),
        encoding="utf-8",
    )
    demand_file.write_text(
        json.dumps(
            {
                "request_id": "request-demand",
                "capital_group_id": "group-1",
                "demand_id": "demand-file",
            }
        ),
        encoding="utf-8",
    )
    cancel_file.write_text(
        json.dumps(
            {
                "request_id": "request-cancel",
                "capital_group_id": "group-1",
                "objective_id": "objective-1",
                "expected_version": 3,
                "strategy_id": "strategy-1",
                "observed_at_unix_nanos": 10,
            }
        ),
        encoding="utf-8",
    )
    reconcile_file.write_text(
        json.dumps(
            {
                "request_id": "request-reconcile",
                "capital_group_id": "group-1",
                "plan_id": "plan-1",
                "observed_at_unix_nanos": 11,
            }
        ),
        encoding="utf-8",
    )

    def run(_self, component, arguments):
        calls.append((component, arguments, _self.workspace.paths.root))
        return {"status": "ok"}

    monkeypatch.setattr(NativeCliApplication, "run", run)

    for argv, expected_arguments in (
        (
            [
                "publish-funding-objective",
                "--file",
                str(objective_file),
            ],
            ["connected", "publish-funding-objective", "--file", str(objective_file)],
        ),
        (
            [
                "observe-demand",
                "--file",
                str(demand_file),
            ],
            ["connected", "observe-demand", "--file", str(demand_file)],
        ),
        (
            [
                "cancel-funding-objective",
                "--file",
                str(cancel_file),
            ],
            ["connected", "cancel-funding-objective", "--file", str(cancel_file)],
        ),
        (
            [
                "reconcile-plan",
                "--file",
                str(reconcile_file),
            ],
            ["connected", "reconcile-plan", "--file", str(reconcile_file)],
        ),
    ):
        output = StringIO()
        assert (
            execute_argv(
                [
                    "system",
                    "component",
                    "capital",
                    *argv,
                    "--workspace",
                    str(workspace.paths.root),
                    "--format",
                    "json",
                ],
                output,
            )
            == 0
        )
        assert json.loads(output.getvalue()) == {"status": "ok"}
        assert calls[-1] == ("capital", expected_arguments, workspace.paths.root)


def test_system_restart_refuses_market_with_active_launch_dependents(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="market-restart"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.component_manifest().write_text(
        '{"components":{"market":{"socket":"%s"}},"accounts":{}}'
        % workspace.paths.process_socket("market"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        LaunchControlApplication,
        "status",
        lambda _self, _target: {"status": "ready"},
    )

    def restart(*_args, **_kwargs):
        raise AssertionError("restart should be blocked before process control")

    monkeypatch.setattr(ComponentProcessApplication, "restart", restart)
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "restart",
                "--component",
                "market",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        != 0
    )

    text = output.getvalue()
    assert "restart refused" in text
    assert "btc / paper / run-1" in text


def test_notifications_validate_reads_workspace_resources(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="n")
    output = StringIO()

    assert (
        execute_argv(
            [
                "notifications",
                "validate",
                "--mode",
                "backtest",
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
        "valid": True,
        "path": str(workspace.paths.notification_config()),
        "issues": [],
    }


def test_project_scaffold_cli_recovers_an_existing_empty_project(tmp_path) -> None:
    project = tmp_path / "demo"
    WorkspaceApplication().init_project(project, workspace_id="demo")
    output = StringIO()

    assert (
        execute_argv(
            [
                "project",
                "scaffold",
                "--workspace",
                str(project),
                "--template",
                "backtest",
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )

    result = json.loads(output.getvalue())
    assert result["status"] == "scaffolded"
    assert (project / "kairos_demo" / "strategy.py").is_file()


def test_project_init_backtest_cli_creates_a_valid_launch(tmp_path) -> None:
    project = tmp_path / "demo"
    output = StringIO()

    assert (
        execute_argv(
            [
                "project",
                "init",
                str(project),
                "--id",
                "demo",
                "--non-interactive",
                "--template",
                "BACKTEST",
            ],
            output,
        )
        == 0
    )
    validate_output = StringIO()
    assert (
        execute_argv(
            [
                "launch",
                "diagnose",
                "validate",
                "demo-backtest",
                "--workspace",
                str(project),
                "--format",
                "json",
            ],
            validate_output,
        )
        == 0
    )
    assert json.loads(validate_output.getvalue())["valid"] is True


def test_project_init_does_not_inherit_an_ambient_workspace_format(
    tmp_path, monkeypatch
) -> None:
    ambient = WorkspaceApplication().init_project(
        tmp_path / "ambient", workspace_id="ambient"
    )
    ambient.paths.manifest.write_text(
        'version = 1\nworkspace_id = "ambient"\n\n[cli]\nformat = "table"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(ambient.paths.project_root)
    output = StringIO()

    assert (
        execute_argv(
            [
                "project",
                "init",
                str(tmp_path / "new-project"),
                "--id",
                "new-project",
                "--non-interactive",
                "--template",
                "backtest",
            ],
            output,
        )
        == 0
    )

    assert output.getvalue().startswith("next_steps:")
    assert "+" not in output.getvalue().splitlines()[0]


def test_generated_backtest_start_assembles_only_offline_runtime_components(
    tmp_path, monkeypatch
) -> None:
    project = tmp_path / "demo"
    WorkspaceApplication().init_project(
        project, workspace_id="demo", template="backtest"
    )
    started_components: list[tuple[str, dict[str, object]]] = []
    resumed_replay: list[str] = []

    class ComponentControl:
        def __init__(self, component: str) -> None:
            self.component = component

        def resume_replay(self) -> None:
            resumed_replay.append(self.component)

    def ensure_component(_self, component, **options):
        started_components.append((component, options))
        return ComponentControl(component)

    monkeypatch.setattr(ComponentProcessApplication, "ensure_running", ensure_component)
    monkeypatch.setattr(
        AccountAdminApplication,
        "show",
        lambda _self, account_id: {
            "account_id": account_id,
            "broker": "paper",
            "environment": "paper",
        },
    )
    monkeypatch.setattr(
        StrategyProcessController,
        "ensure_running",
        lambda _self, *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        LaunchControlApplication,
        "status",
        lambda _self, _target: {"status": "not_running"},
    )
    monkeypatch.setattr(
        LaunchControlApplication,
        "start",
        lambda _self, _target: {"status": "ready"},
    )
    monkeypatch.setattr(
        LaunchControlApplication,
        "strategy_control",
        lambda _self, _target, _action: {"status": "running"},
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "start",
                "demo-backtest",
                "--workspace",
                str(project),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )

    names = [name for name, _options in started_components]
    assert names == ["market", "account", "risk", "execution"]
    market_options = started_components[0][1]
    assert market_options["market_runtime_profile"] == "replay"
    instance = market_options["instance_workspace"]
    assert isinstance(instance, InstanceWorkspace)
    assert instance.market_state("replay.jsonl").is_file()
    assert "reference" not in names
    assert resumed_replay == ["market"]
    assert json.loads(output.getvalue())["next_action"] == (
        "kairos launch wait demo-backtest"
    )


def test_generated_market_data_replays_through_canonical_market_cli(tmp_path) -> None:
    project = tmp_path / "demo"
    workspace = WorkspaceApplication().init_project(
        project, workspace_id="demo", template="backtest"
    )
    events = workspace.paths.data_root() / "examples" / "demo-market.jsonl"
    output = StringIO()

    assert (
        execute_argv(
            [
                "market",
                "replay",
                "--workspace",
                str(project),
                "--file",
                str(events),
                "--market-id",
                "market:binance:spot:BTCUSDT",
                "--instrument-id",
                "instrument:binance:spot:BTCUSDT",
                "--output",
                "json",
            ],
            output,
        )
        == 0
    )

    result = json.loads(output.getvalue())
    assert result["events_applied"] == 5
    assert result["snapshot"]["event_sequence"] == 5


def test_generated_paper_account_loads_through_canonical_account_cli(tmp_path) -> None:
    project = tmp_path / "demo"
    WorkspaceApplication().init_project(
        project, workspace_id="demo", template="backtest"
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "account",
                "--workspace",
                str(project),
                "--output",
                "json",
                "show",
                "--account-id",
                "demo-paper",
            ],
            output,
        )
        == 0
    )

    account = json.loads(output.getvalue())
    assert account["broker"] == "paper"
    assert account["environment"] == "paper"
    assert account["initial_balances"] == ["USDT=100000"]

    output = StringIO()
    assert (
        execute_argv(
            [
                "account",
                "--workspace",
                str(project),
                "--account-id",
                "demo-paper",
                "standalone",
                "balances",
                "--output",
                "table",
            ],
            output,
        )
        == 0
    )
    balances = output.getvalue()
    assert "分区" in balances
    assert "资产" in balances
    assert "总额" in balances
    assert "USDT" in balances
    assert "100000" in balances
    assert "balances" not in balances


def test_launch_start_missing_config_points_to_project_doctor(tmp_path) -> None:
    project = tmp_path / "demo"
    WorkspaceApplication().init_project(project, workspace_id="demo")
    output = StringIO()

    assert (
        execute_argv(
            ["launch", "start", "missing", "--workspace", str(project)], output
        )
        != 0
    )

    text = output.getvalue()
    assert "config/launches/missing.toml" in text
    assert "kairos project doctor" in text


def test_launch_start_does_not_expose_internal_instance_or_strategy_root_options() -> (
    None
):
    output = StringIO()

    assert execute_argv(["launch", "start", "--help"], output) == 0
    text = output.getvalue()
    assert "--instance" not in text
    assert "--mode" not in text
    assert "--strategy-root" not in text


def test_static_backtest_replay_does_not_require_reference_runtime() -> None:
    assert _requires_reference_runtime("backtest", True) is False
    assert _requires_reference_runtime("backtest", False) is True
    assert _requires_reference_runtime("paper", True) is True


def test_launch_status_omits_explicitly_optional_reference(
    tmp_path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="backtest"
    )
    instance = workspace.instance("backtest", "demo", "run-1")
    instance.prepare()
    instance.component_manifest().write_text(
        '{"components":{"reference":{"required":false}},"accounts":{}}',
        encoding="utf-8",
    )

    def component_status(_self, component, **_kwargs):
        return {"component": component, "status": "ready"}

    monkeypatch.setattr(ComponentProcessApplication, "status", component_status)

    result = _decorate_launch_status(
        workspace, "demo", "run-1", "backtest", {"status": "ready"}
    )

    assert "reference" not in result["component_status"]
    assert result["launch_status"] == "healthy"


def test_launch_control_commands_do_not_require_mode_flag() -> None:
    for command in ("status", "logs", "attach", "stop", "restart", "artifacts"):
        output = StringIO()
        assert execute_argv(["launch", command, "--help"], output) == 0
        assert "--mode" not in output.getvalue()

    output = StringIO()
    assert execute_argv(["launch", "attach", "--help"], output) == 0
    assert "--python" in output.getvalue()

    for command in ("status", "decision", "enable", "pause", "resume", "refresh"):
        output = StringIO()
        assert execute_argv(["launch", "strategy", command, "--help"], output) == 0
        assert "--mode" not in output.getvalue()


def test_launch_strategy_decision_queries_instance_control(
    tmp_path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="decision"
    )
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")

    def decision(_self, target, strategy_decision_id):
        return {
            "launch_id": target.launch_id,
            "instance_id": target.instance_id,
            "strategy_decision_id": strategy_decision_id,
        }

    monkeypatch.setattr(LaunchControlApplication, "decision", decision)
    output = StringIO()
    assert (
        execute_argv(
            [
                "launch",
                "strategy",
                "decision",
                "btc",
                "decision:1",
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
        "launch_id": "btc",
        "instance_id": "run-1",
        "strategy_decision_id": "decision:1",
    }


def test_launch_diagnose_uses_config_identity_without_runtime_flags() -> None:
    for command in ("validate", "explain"):
        output = StringIO()
        assert execute_argv(["launch", "diagnose", command, "--help"], output) == 0
        text = output.getvalue()
        assert "--mode" not in text
        assert "--instance" not in text


def test_launch_instance_ids_are_opaque_uuids() -> None:
    first = new_instance_id()
    second = new_instance_id()

    import uuid

    assert uuid.UUID(first).version == 4
    assert uuid.UUID(second).version == 4
    assert first != second


def test_launch_registry_records_lifecycle_timestamps(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "demo", workspace_id="demo"
    )
    registry = LaunchRegistryApplication(workspace)

    created = registry.add("demo", mode="backtest", instance_id="run-1")
    updated = registry.update_state(
        "demo", mode="backtest", instance_id="run-1", state="running"
    )

    assert created["created_at"]
    assert created["updated_at"]
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] >= created["updated_at"]


def test_cli_registers_legacy_product_groups() -> None:
    output = StringIO()

    assert execute_argv(["--help"], output) == 0
    text = output.getvalue()
    for command in (
        "project",
        "config",
        "data",
        "launch",
        "account",
        "integration",
        "market",
        "reference",
        "order",
        "system",
    ):
        assert command in text
    assert "catalog" not in text
    assert not any("│ shell " in line for line in text.splitlines())
    panels = (
        "Getting started",
        "Strategy workflow",
        "Research & data",
        "System operations",
        "Business tools",
        "Advanced tools",
    )
    assert all(panel in text for panel in panels)
    assert [text.index(panel) for panel in panels] == sorted(
        text.index(panel) for panel in panels
    )
    assert "quickstart" in text
    assert "Run strategies and inspect" in text
    assert all(mode in text for mode in ("backtest", "paper", "live"))
    assert "New to Kairos?" in text
    assert "unified workbench" in text
    assert "commands are owned by" not in text


def test_cli_help_stays_compact_and_uses_canonical_program_name(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "180")
    output = StringIO()

    assert execute_argv(["--help"], output, prog_name="kairos") == 0
    text = output.getvalue()

    assert "Usage: kairos " in text
    assert max(len(line) for line in text.splitlines()) <= 100


def test_quickstart_shows_first_run_path() -> None:
    output = StringIO()

    assert execute_argv(["quickstart"], output) == 0
    text = output.getvalue()
    assert "KairosPy quickstart" in text
    assert "kairos project init my-project --id my-project --template backtest" in text
    assert "kairos launch start demo-backtest" in text
    assert "Command map" in text


def test_quickstart_supports_json_output() -> None:
    output = StringIO()

    assert execute_argv(["quickstart", "--format", "json"], output) == 0
    value = json.loads(output.getvalue())
    assert value["first_run"][0]["command"].startswith("kairos project init")
    assert value["command_map"]["launch"].startswith("Start")


def test_cli_format_falls_back_to_text_without_workspace(monkeypatch, tmp_path) -> None:
    from kairospy.surface.cli.app import _cli_format

    monkeypatch.delenv("KAIROS_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)

    assert _cli_format(["reference", "health"]) == "text"


def test_interactive_is_discoverable_from_top_level_help() -> None:
    output = StringIO()

    assert execute_argv(["--help"], output) == 0
    text = output.getvalue()
    assert "interactive" in text
    assert "Kairos Textual 工作台" in text


def test_interactive_opens_workbench_even_before_workspace_exists(monkeypatch) -> None:
    from kairospy.surface.cli import app as cli_module

    seen: list[object] = []
    monkeypatch.setattr(
        cli_module,
        "run_workbench",
        lambda request: seen.append(request) or SimpleNamespace(transcript_path=None),
    )

    cli_module._interactive_command(None, False, False, False)

    assert len(seen) == 1
    request = seen[0]
    assert request.workspace is None
    assert request.require_workspace is False


def test_interactive_passes_safety_flags_to_public_workbench_launcher(
    monkeypatch,
) -> None:
    from kairospy.surface.cli import app as cli_module

    seen: list[object] = []
    monkeypatch.setattr(
        cli_module,
        "run_workbench",
        lambda request: seen.append(request) or SimpleNamespace(transcript_path=None),
    )

    cli_module._interactive_command("workspace", True, True, True)

    request = seen[0]
    assert request.workspace == Path("workspace")
    assert request.dry_run is True
    assert request.no_exec is True
    assert request.yes is True


def test_interactive_can_preserve_the_terminal_screen(monkeypatch) -> None:
    from kairospy.surface.cli import app as cli_module

    seen: list[object] = []
    monkeypatch.setattr(
        cli_module,
        "run_workbench",
        lambda request: seen.append(request) or SimpleNamespace(transcript_path=None),
    )

    cli_module._interactive_command(None, False, False, False, no_alt_screen=True)

    assert seen[0].inline is True


def test_account_query_balance_uses_top_level_standalone_mode(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "demo", workspace_id="demo"
    )
    seen: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = '{"mode":"standalone","source":"direct_provider"}'
        stderr = ""

    def invoke(_self, arguments):
        seen.append(list(arguments))
        return Result()

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.account.AccountCliApplication.invoke", invoke
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "account",
                "query",
                "balance",
                "live-main",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        == 0
    )
    assert seen == [["--account-id", "live-main", "standalone", "balance"]]
    assert "connected" not in seen[0]


def test_account_positions_uses_top_level_standalone_mode(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "demo", workspace_id="demo"
    )
    seen: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = '{"mode":"standalone","source":"direct_provider","positions":[]}'
        stderr = ""

    def invoke(_self, arguments):
        seen.append(list(arguments))
        return Result()

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.account.AccountCliApplication.invoke", invoke
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "account",
                "positions",
                "live-main",
                "--segment",
                "usd_m_futures",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        == 0
    )
    assert seen == [
        [
            "--account-id",
            "live-main",
            "standalone",
            "positions",
            "--segment",
            "usd_m_futures",
        ]
    ]


def test_readme_uses_the_current_golden_path_commands() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert "project init my-project" in readme
    assert "--template backtest" in readme
    assert "launch wait demo-backtest" in readme
    assert "kairospy shell" not in readme
    assert "kairospy catalog" not in readme


def test_readme_local_links_resolve() -> None:
    root = Path(__file__).parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    targets = [
        match.group(1)
        for match in re.finditer(r"\]\(([^)#]+)(?:#[^)]*)?\)", readme)
        if not match.group(1).startswith(("http://", "https://"))
    ]

    missing = [target for target in targets if not (root / target).exists()]
    assert missing == []


def test_cli_exposes_canonical_business_command_surfaces() -> None:
    for argv, expected in (
        (["account", "--help"], ("credential-list", "simulate", "schema")),
        (["integration", "--help"], ("transfer", "earn")),
        (["market", "--help"], ("validate", "once", "replay")),
        (["risk", "--help"], ("schema", "doctor", "preview")),
        (["capital", "--help"], ("schema", "doctor")),
        (["launch", "--help"], ("targets", "diagnose", "replay", "instance")),
        (
            ["reference", "--help"],
            ("health", "catalog", "assets", "listings", "markets"),
        ),
        (["system", "--help"], ("component", "restart", "list")),
        (
            ["system", "component", "--help"],
            ("market", "reference", "risk", "capital"),
        ),
    ):
        output = StringIO()
        assert execute_argv(argv, output) == 0
        text = output.getvalue()
        for command in expected:
            assert command in text


def test_system_restart_progress_is_text_only(tmp_path, monkeypatch) -> None:
    project = tmp_path / "demo"
    WorkspaceApplication().init_project(project, workspace_id="demo")

    class Control:
        @staticmethod
        def status() -> dict[str, str]:
            return {"status": "ready"}

    def restart(_self, component, **options):
        progress = options.get("progress")
        if progress is not None:
            progress(f"Waiting for {component} to stop...")
        return Control()

    monkeypatch.setattr(ComponentProcessApplication, "restart", restart)
    monkeypatch.setattr(SystemRuntimeSupervisor, "register", lambda *_a, **_k: None)
    monkeypatch.setattr(
        SystemRuntimeSupervisor, "start_background", lambda *_a, **_k: None
    )

    text_output = StringIO()
    assert (
        execute_argv(
            [
                "system",
                "restart",
                "--component",
                "reference",
                "--workspace",
                str(project),
                "--format",
                "text",
            ],
            text_output,
        )
        == 0
    )
    assert "Waiting for reference to stop..." in text_output.getvalue()

    json_output = StringIO()
    assert (
        execute_argv(
            [
                "system",
                "restart",
                "--component",
                "reference",
                "--workspace",
                str(project),
                "--format",
                "json",
            ],
            json_output,
        )
        == 0
    )
    assert json.loads(json_output.getvalue()) == {"status": "ready"}


def test_system_list_renders_a_prettytable_when_requested(tmp_path) -> None:
    WorkspaceApplication().init_project(tmp_path / "demo", workspace_id="demo")
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "list",
                "--workspace",
                str(tmp_path / "demo"),
                "--format",
                "table",
            ],
            output,
        )
        == 0
    )

    text = output.getvalue()
    assert "+" in text
    assert "component" in text
    assert "reference" in text


def test_system_list_keeps_json_output_machine_readable(tmp_path) -> None:
    WorkspaceApplication().init_project(tmp_path / "demo", workspace_id="demo")
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "list",
                "--workspace",
                str(tmp_path / "demo"),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )

    import json

    value = json.loads(output.getvalue())
    assert value["reference"]["status"] == "not_running"
    assert "pid" in value["reference"]
    assert "log_file" in value["reference"]


def test_table_output_renders_list_of_dicts() -> None:
    text = render([{"symbol": "BTCUSDT", "status": "active"}], OutputFormat.TABLE)

    assert "+" in text
    assert "symbol" in text
    assert "BTCUSDT" in text


def test_workspace_output_format_is_used_by_local_cli_commands(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "demo", workspace_id="demo"
    )
    workspace.paths.manifest.write_text(
        'version = 1\nworkspace_id = "demo"\n\n[cli]\nformat = "table"\n',
        encoding="utf-8",
    )
    output = StringIO()

    assert (
        execute_argv(
            ["project", "status", "--workspace", str(tmp_path / "demo")], output
        )
        == 0
    )
    assert output.getvalue().lstrip().startswith("+")


def test_explicit_output_format_overrides_workspace_default(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "demo", workspace_id="demo"
    )
    workspace.paths.manifest.write_text(
        'version = 1\nworkspace_id = "demo"\n\n[cli]\nformat = "table"\n',
        encoding="utf-8",
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "project",
                "status",
                "--workspace",
                str(tmp_path / "demo"),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    import json

    assert json.loads(output.getvalue())["workspace_id"] == "demo"


def test_system_logs_reads_component_process_output(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "demo", workspace_id="demo"
    )
    log = workspace.paths.logs / "market" / "process.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("first\nsecond\n", encoding="utf-8")
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "logs",
                "market",
                "--lines",
                "1",
                "--workspace",
                str(tmp_path / "demo"),
                "--format",
                "text",
            ],
            output,
        )
        == 0
    )

    assert output.getvalue().strip() == "second"


def test_system_logs_accepts_component_option(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "demo", workspace_id="demo"
    )
    log = workspace.paths.logs / "reference" / "process.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("ready\n", encoding="utf-8")
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "logs",
                "--component",
                "reference",
                "--workspace",
                str(tmp_path / "demo"),
                "--format",
                "text",
            ],
            output,
        )
        == 0
    )
    assert output.getvalue().strip() == "ready"


def test_system_logs_rejects_conflicting_component_values() -> None:
    output = StringIO()

    assert (
        execute_argv(["system", "logs", "market", "--component", "reference"], output)
        != 0
    )
    assert "specified twice with different values" in output.getvalue()


def test_system_logs_filters_current_structured_run(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "demo", workspace_id="demo"
    )
    log = workspace.paths.logs / "reference" / "process.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    values = [
        {
            "event": "reference_provider_unavailable",
            "level": "WARN",
            "provider": "massive-equity",
            "run_id": "old",
        },
        {"event": "process_spawned", "level": "INFO", "run_id": "new"},
        {
            "event": "reference_provider_unavailable",
            "level": "WARN",
            "provider": "massive-equity",
            "run_id": "new",
        },
        {"event": "reference_refresh_completed", "level": "INFO", "run_id": "new"},
    ]
    log.write_text("\n".join(json.dumps(value) for value in values) + "\n")
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "logs",
                "--component",
                "reference",
                "--current-run",
                "--level",
                "warn",
                "--provider",
                "massive-equity",
                "--workspace",
                str(tmp_path / "demo"),
                "--format",
                "text",
            ],
            output,
        )
        == 0
    )
    rendered = [json.loads(line) for line in output.getvalue().splitlines()]
    assert rendered == [values[2]]


def test_system_doctor_reports_stale_health_pid(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "demo", workspace_id="demo"
    )
    health = workspace.paths.health_file("reference")
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text('{"status":"ready","pid":999999}', encoding="utf-8")
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "doctor",
                "--workspace",
                str(tmp_path / "demo"),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )

    import json

    value = json.loads(output.getvalue())
    assert value["components"]["reference"]["status"] == "stale"
    assert value["components"]["reference"]["pid_alive"] is False


def test_cli_version_is_script_friendly() -> None:
    output = StringIO()

    assert execute_argv(["version"], output) == 0
    assert output.getvalue().strip() == "kairospy 0.1.0"


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
