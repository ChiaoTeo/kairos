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


def test_launch_instance_component_execution_active_orders_uses_connected_owner_cli(
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
                "active-orders",
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
            "active-orders",
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
    from decimal import Decimal
    from types import SimpleNamespace

    from kairospy.system.apps.components.application.clients import RiskSystemClient

    class FakeCurrentView:
        path = tmp_path / "snapshots" / "risk"

        def snapshot(self):
            scope = SimpleNamespace(
                account_id="main",
                strategy_id=None,
                instrument_id=None,
                exchange_id=None,
            )

            def amount(value: str) -> SimpleNamespace:
                return SimpleNamespace(value=Decimal(value))

            policy = SimpleNamespace(
                policy_id="policy-1",
                version=2,
                scope=scope,
                metric="notional",
                limit=amount("100"),
                enforcement="hard",
                valid_from_unix_nanos=1,
                valid_until_unix_nanos=None,
                window_nanos=None,
            )
            limit = SimpleNamespace(
                policy=policy,
                used=amount("20"),
                reserved=amount("5"),
                available=amount("75"),
            )
            reservation = SimpleNamespace(
                reservation_id="reservation-1",
                request_id="request-1",
                account_id="main",
                strategy_id="strategy-1",
                idempotency_key="idempotency-1",
                allocations=[],
                status="reserved",
                created_at_unix_nanos=1,
                updated_at_unix_nanos=2,
                expires_at_unix_nanos=3,
                policy_version=2,
            )
            circuit = SimpleNamespace(
                circuit_id="circuit-1",
                scope=scope,
                status="open",
                opened_at_unix_nanos=1,
                reset_at_unix_nanos=None,
                reason="test",
            )
            return SimpleNamespace(
                actor_id="risk:run-1",
                generation=3,
                policy_version=2,
                limits=[limit],
                reservations=[reservation],
                circuits=[circuit],
                applied_event_sequence=9,
            )

    seen: dict[str, object] = {}

    def latest_view(self, *, actor_id):
        seen["actor_id"] = actor_id
        return FakeCurrentView()

    monkeypatch.setattr(RiskSystemClient, "latest_view", latest_view)

    client = RiskSystemClient(
        tmp_path / "risk.sock",
        view_root=tmp_path / "snapshots",
        workspace_id="workspace",
    )
    latest = client.latest(actor_id="risk:run-1")
    assert latest["generation"] == 3
    assert latest["limits"][0]["policy"]["policy_id"] == "policy-1"
    assert latest["active_reservations"][0]["reservation_id"] == "reservation-1"
    assert latest["circuits"][0]["circuit_id"] == "circuit-1"
    assert latest["summary"] == {
        "limit_count": 1,
        "active_reservation_count": 1,
        "open_circuit_count": 1,
    }
    assert client.latest_metadata(actor_id="risk:run-1") == client.latest(
        actor_id="risk:run-1"
    )
    assert (
        client.latest_limits(actor_id="risk:run-1")["limits"][0]["policy"]["policy_id"]
        == "policy-1"
    )
    assert (
        client.latest_reservations(actor_id="risk:run-1")["active_reservations"][0][
            "reservation_id"
        ]
        == "reservation-1"
    )
    assert (
        client.latest_circuits(actor_id="risk:run-1")["circuits"][0]["circuit_id"]
        == "circuit-1"
    )
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
        def snapshot(self):
            return SimpleNamespace(
                capital_group_id="group-1",
                generation=4,
                availabilities=({"readiness": "degraded"},),
                objectives=({"objective_id": "objective-1"},),
                demands=({"demand_id": "demand-1"},),
                policies=(),
                facts=(),
                plans=({"plan_id": "plan-1"},),
                routes=({"route_id": "route-1"},),
                reservations=({"reservation_id": "reservation-1"},),
                operations=({"operation_id": "operation-1"},),
                alerts=({"severity": "critical"},),
            )

    seen: dict[str, object] = {}

    def current_view(self, capital_group_id):
        seen["capital_group_id"] = capital_group_id
        return FakeCurrentView()

    monkeypatch.setattr(CapitalSystemClient, "current_view", current_view)

    client = CapitalSystemClient(
        tmp_path / "capital.sock", view_root=tmp_path / "snapshots"
    )
    assert client.current("group-1").generation == 4
    assert client.current_metadata("group-1").capital_group_id == "group-1"
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
