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
            return ReferenceOptionCoverage(
                ReferenceSourceIdRead("massive-options"),
                GenerationRead(0),
                SequenceRead(0),
                (InstrumentIdRead("SPY"),),
            )

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
    assert json.loads(output.getvalue()) == {
        "source_id": "massive-options",
        "generation": 0,
        "event_sequence": 0,
        "underlyings": ["SPY"],
    }

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
