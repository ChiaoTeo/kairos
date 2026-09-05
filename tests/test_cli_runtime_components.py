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
            return ReferenceHealthResponse("ready", ())

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
    assert value["providers"] == []
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
        def snapshot(self):
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
    assert seen == {"current_view_account_id": "main"}


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
        def snapshot(self):
            decimal = SimpleNamespace(value=Decimal("1"))
            return SimpleNamespace(
                observed_orders=(
                    SimpleNamespace(
                        observation_id="observation-1",
                        source_id="source-1",
                        execution_order_id="order-1",
                        remote_order_id="remote-1",
                        instrument_id="instrument-1",
                        market_id="market-1",
                        side="buy",
                        quantity=decimal,
                        filled_quantity=decimal,
                        status="open",
                        observed_at_unix_nanos=1,
                        segment_key="spot",
                    ),
                )
            )

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
        "open_orders": [
            {
                "observation_id": "observation-1",
                "source_id": "source-1",
                "execution_order_id": "order-1",
                "remote_order_id": "remote-1",
                "instrument_id": "instrument-1",
                "market_id": "market-1",
                "side": "buy",
                "quantity": "1",
                "filled_quantity": "1",
                "status": "open",
                "observed_at_unix_nanos": 1,
                "segment_key": "spot",
            }
        ],
        "launch_id": "btc",
        "instance_id": "run-1",
        "mode": "paper",
        "scope": "launch-instance",
    }
    assert seen == {"current_view_account_id": "main"}


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
