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
