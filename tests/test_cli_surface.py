from __future__ import annotations

from io import StringIO

from kairospy.application.launch.application import LaunchControlApplication
from kairospy.application.launch.application import new_instance_id
from kairospy.application.timeline import TimelineApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli import execute_argv


def test_cli_exposes_legacy_launch_entry_shape() -> None:
    output = StringIO()

    assert execute_argv(["launch", "--help"], output) == 0
    text = output.getvalue()
    assert "start" in text
    assert "status" in text
    assert "strategy" in text


def test_launch_start_does_not_expose_internal_instance_or_strategy_root_options() -> None:
    output = StringIO()

    assert execute_argv(["launch", "start", "--help"], output) == 0
    text = output.getvalue()
    assert "--instance" not in text
    assert "--strategy-root" not in text


def test_launch_instance_ids_are_opaque_uuids() -> None:
    first = new_instance_id()
    second = new_instance_id()

    import uuid
    assert uuid.UUID(first).version == 4
    assert uuid.UUID(second).version == 4
    assert first != second


def test_cli_registers_legacy_product_groups() -> None:
    output = StringIO()

    assert execute_argv(["--help"], output) == 0
    text = output.getvalue()
    for command in ("project", "config", "launch", "account", "market", "reference", "order", "system", "timeline"):
        assert command in text
    assert "catalog" not in text


def test_cli_exposes_canonical_business_command_surfaces() -> None:
    for argv, expected in (
        (["account", "--help"], ("credential-list", "balances", "snapshot")),
        (["market", "--help"], ("validate", "once", "replay")),
        (["launch", "--help"], ("targets", "diagnose", "replay")),
        (["reference", "--help"], ("health", "snapshot", "markets")),
        (["system", "--help"], ("account", "restart", "list")),
    ):
        output = StringIO()
        assert execute_argv(argv, output) == 0
        text = output.getvalue()
        for command in expected:
            assert command in text


def test_system_list_renders_a_prettytable_by_default(tmp_path) -> None:
    WorkspaceApplication().init_project(tmp_path / "demo", workspace_id="demo")
    output = StringIO()

    assert execute_argv(["system", "list", "--workspace", str(tmp_path / "demo")], output) == 0

    text = output.getvalue()
    assert "+" in text
    assert "component" in text
    assert "reference" in text


def test_system_list_keeps_json_output_machine_readable(tmp_path) -> None:
    WorkspaceApplication().init_project(tmp_path / "demo", workspace_id="demo")
    output = StringIO()

    assert execute_argv(
        ["system", "list", "--workspace", str(tmp_path / "demo"), "--format", "json"],
        output,
    ) == 0

    import json
    value = json.loads(output.getvalue())
    assert value["reference"]["status"] == "not_running"



def test_cli_version_is_script_friendly() -> None:
    output = StringIO()

    assert execute_argv(["version"], output) == 0
    assert output.getvalue().strip() == "kairospy 0.1.0"


def test_project_init_creates_dot_kairos_workspace(tmp_path) -> None:
    output = StringIO()

    assert execute_argv(["project", "init", str(tmp_path / "demo"), "--id", "demo"], output) == 0
    assert (tmp_path / "demo" / ".kairos" / "kairos.toml").exists()
    assert not (tmp_path / "demo" / "workspace.toml").exists()


def test_project_init_non_interactive_requires_explicit_inputs() -> None:
    output = StringIO()

    assert execute_argv(["project", "init", "--non-interactive"], output) != 0
    assert "project directory is required" in output.getvalue()


def test_project_init_prompts_for_project_name_and_directory(tmp_path, monkeypatch) -> None:
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
    assert target.socket_path == workspace.paths.launch_socket("paper", "btc-paper", "instance-1")


def test_timeline_application_reads_and_exports_jsonl(tmp_path) -> None:
    source = tmp_path / "events.jsonl"
    source.write_text('{"sequence": 1, "kind": "started"}\n{"sequence": 2, "kind": "stopped"}\n', encoding="utf-8")
    assert TimelineApplication().list(source, limit=1) == [{"sequence": 2, "kind": "stopped"}]
    destination = tmp_path / "export.jsonl"
    assert TimelineApplication().export(source, destination) == destination
    assert destination.read_text(encoding="utf-8").count("sequence") == 2
