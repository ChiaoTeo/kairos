from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import re

from kairospy.application.launch.application import (
    LaunchControlApplication,
    LaunchRegistryApplication,
)
from kairospy.application.launch.application import new_instance_id
from kairospy.application.timeline import TimelineApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli import execute_argv
from kairospy.surface.cli.commands.launch import (
    _decorate_launch_status,
    _resolve_launch_target,
    _resolve_instance,
    _resolve_stop_instance,
    _requires_reference_runtime,
    _stop_component_safely,
)
from kairospy.application.system import ComponentProcessApplication
from kairospy.application.account import AccountAdminApplication
from kairospy.application.strategy import StrategyProcessApplication
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

    def ensure_component(_self, component, **options):
        started_components.append((component, options))
        return object()

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
        StrategyProcessApplication,
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
    assert market_options["market_provider"] == "replay"
    assert Path(market_options["market_replay_file"]).is_file()
    assert "reference" not in names
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
    assert account["provider"] == "paper"
    assert account["environment"] == "paper"
    assert account["initial_balances"] == ["USDT=100000"]


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
    assert _requires_reference_runtime("backtest", "replay") is False
    assert _requires_reference_runtime("backtest", "workspace") is True
    assert _requires_reference_runtime("paper", "replay") is True


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
    for command in ("status", "logs", "attach", "stop", "artifacts"):
        output = StringIO()
        assert execute_argv(["launch", command, "--help"], output) == 0
        assert "--mode" not in output.getvalue()

    for command in ("status", "enable", "pause", "resume", "refresh"):
        output = StringIO()
        assert execute_argv(["launch", "strategy", command, "--help"], output) == 0
        assert "--mode" not in output.getvalue()


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
        "launch",
        "account",
        "integration",
        "market",
        "reference",
        "order",
        "system",
        "timeline",
    ):
        assert command in text
    assert "catalog" not in text
    assert not any("│ shell " in line for line in text.splitlines())
    assert "Daily workflow" in text
    assert "Operations" in text
    assert "Advanced tools" in text
    assert "Run strategies and inspect" in text
    assert "commands are owned by" not in text


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
        (["account", "--help"], ("credential-list", "balances", "snapshot")),
        (["integration", "--help"], ("transfer", "earn")),
        (["market", "--help"], ("validate", "once", "replay")),
        (["launch", "--help"], ("targets", "diagnose", "replay")),
        (
            ["reference", "--help"],
            ("health", "snapshots", "catalog", "assets", "listings", "markets"),
        ),
        (["system", "--help"], ("account", "restart", "list")),
    ):
        output = StringIO()
        assert execute_argv(argv, output) == 0
        text = output.getvalue()
        for command in expected:
            assert command in text


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
    log = workspace.paths.logs / "processes" / "market.log"
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


def test_project_init_prompts_for_project_name_and_directory(
    tmp_path, monkeypatch
) -> None:
    output = StringIO()
    answers = iter([str(tmp_path / "demo"), "custom-project"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))

    assert execute_argv(["project", "init"], output) == 0
    manifest = tmp_path / "demo" / ".kairos" / "kairos.toml"
    assert manifest.exists()
    assert 'workspace_id = "custom-project"' in manifest.read_text()


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


def test_launch_commands_resolve_latest_instance_when_instance_is_omitted(
    tmp_path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="resolve"
    )
    registry = LaunchRegistryApplication(workspace)
    registry.add("btc-options", mode="paper", instance_id="run-1")
    registry.add("btc-options", mode="paper", instance_id="run-2")

    assert _resolve_instance(workspace, "btc-options", "paper", None) == "run-2"
    assert _resolve_instance(workspace, "btc-options", "paper", "run-1") == "run-1"


def test_launch_target_resolution_discovers_non_paper_mode(tmp_path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="mode")
    LaunchRegistryApplication(workspace).add(
        "btc", mode="backtest", instance_id="run-1"
    )

    assert _resolve_launch_target(workspace, "btc", None, None) == ("run-1", "backtest")


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


def test_timeline_application_reads_and_exports_jsonl(tmp_path) -> None:
    source = tmp_path / "events.jsonl"
    source.write_text(
        '{"sequence": 1, "kind": "started"}\n{"sequence": 2, "kind": "stopped"}\n',
        encoding="utf-8",
    )
    assert TimelineApplication().list(source, limit=1) == [
        {"sequence": 2, "kind": "stopped"}
    ]
    destination = tmp_path / "export.jsonl"
    assert TimelineApplication().export(source, destination) == destination
    assert destination.read_text(encoding="utf-8").count("sequence") == 2


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
