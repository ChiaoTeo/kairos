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


def test_system_repair_commands_support_the_workbench_service_actions(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="repair-service"
    )
    calls: list[tuple[str, bool]] = []

    def repair(_self, component: str, *, start: bool):
        calls.append((component, start))
        return {"component": component, "status": "ready" if start else "repaired"}

    monkeypatch.setattr(WorkspaceServiceApplication, "repair", repair)
    for command, start in (("repair", False), ("repair-start", True)):
        output = StringIO()
        assert (
            execute_argv(
                [
                    "system",
                    command,
                    "--component",
                    "reference",
                    "--workspace",
                    str(workspace.paths.root),
                    "--format",
                    "json",
                ],
                output,
            )
            == 0
        )
        assert json.loads(output.getvalue())["component"] == "reference"
        assert calls[-1] == ("reference", start)


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
