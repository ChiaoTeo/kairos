"""Launch registry, diagnostics, attachment, logs, and artifacts commands."""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from kairospy.investment.apps.market.application import read_replay_events
from kairospy.surface.cli.options import OutputFormat, effective_output
from kairospy.system.apps.launch.application import (
    LaunchConfigError,
    LaunchConfigurationApplication,
    LaunchControlApplication,
    LaunchRegistryApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import (
    diagnose_app,
    launch_app,
    replay_app,
    targets_app,
)
from .support import (
    _decorate_launch_status,
    _emit,
    _launch_config_path,
    _resolve_launch_target,
    _running_instance,
    _target,
)


def _registry_command(action: str):
    def command(
        launch_id: str | None = typer.Argument(None),
        instance: str = typer.Option("default", "--instance"),
        mode: str = typer.Option("paper", "--mode"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        app = LaunchRegistryApplication(WorkspaceApplication().open(workspace))
        if action in {"list", "browse", "index"}:
            value = (
                app.instances(launch_id)
                if action != "index"
                else {"path": str(app.path), "instances": app.list()}
            )
        elif action == "add":
            if not launch_id:
                raise typer.BadParameter("launch_id is required")
            config_path = Path(launch_id).expanduser()
            if config_path.is_file():
                try:
                    config = LaunchConfigurationApplication().load(
                        config_path, workspace_root=app.workspace.paths.root
                    )
                    config.require_valid()
                except LaunchConfigError as error:
                    raise typer.BadParameter(str(error)) from error
                value = app.add(
                    config.launch_id,
                    mode=config.mode,
                    instance_id=instance,
                    strategy_ref=config.strategy,
                    config_path=config.path,
                )
            else:
                value = app.add(launch_id, mode=mode, instance_id=instance)
        elif action == "remove":
            if not launch_id:
                raise typer.BadParameter("launch_id is required")
            value = app.remove(launch_id, mode=mode, instance_id=instance)
        else:
            value = app.list()
        _emit(value, output)

    command.__name__ = f"launch_target_{action}"
    return command


for _action in ("add", "remove", "index", "list", "browse"):
    targets_app.command(_action)(_registry_command(_action))


def _diagnose(action: str):
    def command(
        launch_id: str,
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        try:
            config_path = _launch_config_path(owner, launch_id)
        except FileNotFoundError as error:
            raise typer.BadParameter(str(error), param_hint="launch_id") from error
        application = LaunchConfigurationApplication()
        try:
            value = (
                application.validate(config_path, workspace_root=owner.paths.root)
                if action == "validate"
                else application.explain(config_path, workspace_root=owner.paths.root)
            )
        except LaunchConfigError as error:
            raise typer.BadParameter(str(error)) from error
        _emit(value, output)

    command.__name__ = f"launch_diagnose_{action}"
    return command


for _action in ("validate", "explain"):
    diagnose_app.command(_action)(_diagnose(_action))


@launch_app.command("instances", help="List current and historical launch instances.")
def instances(
    launch_id: str | None = typer.Argument(None),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(
        LaunchRegistryApplication(WorkspaceApplication().open(workspace)).instances(
            launch_id
        ),
        output,
    )


@launch_app.command("attach", help="Follow launch status and recent strategy output.")
def attach(
    launch_id: str,
    lines: int = typer.Option(
        100, "--lines", min=0, help="Number of recent strategy log lines to show."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    python: bool = typer.Option(
        False,
        "--python",
        help="Open a Python console routed to the running Strategy on this launch.",
    ),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    active = _running_instance(owner, launch_id)
    if active is None:
        raise typer.BadParameter(f"launch is not running: {launch_id}")
    instance = str(active["instance_id"])
    mode = str(active.get("mode") or "paper")
    target = _target(launch_id, instance, mode, workspace)
    log_path = owner.instance(mode, launch_id, instance).log("strategy", "process.log")
    if python:
        if effective_output(output) is not OutputFormat.TEXT:
            raise typer.BadParameter("--python requires text output")
        from kairospy.surface.workbench import WorkbenchLaunchRequest, run_workbench

        run_workbench(
            WorkbenchLaunchRequest(
                workspace=Path(owner.paths.root),
                launch_attach=launch_id,
                require_workspace=True,
            )
        )
        return
    value = _decorate_launch_status(
        owner,
        launch_id,
        instance,
        mode,
        LaunchControlApplication(owner).status(target),
    )
    log_lines = (
        log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        if log_path.is_file() and lines
        else []
    )
    structured_logs = []
    for line in log_lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = {"message": line, "structured": False}
        structured_logs.append(record)
    value.update(
        {
            "socket": str(target.socket_path),
            "mode": mode,
            "launch_id": launch_id,
            "instance_id": instance,
            "stdout_log": str(log_path),
            "stdout": log_lines,
            "logs": structured_logs,
        }
    )
    _emit(value, output)


@launch_app.command("logs", help="Read or follow strategy logs for a launch.")
def logs(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    lines: int = typer.Option(
        100, "--lines", min=0, help="Number of recent log lines to show."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    follow: bool = typer.Option(
        False, "-f", "--follow", help="Follow the selected log file."
    ),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    if follow and effective_output(output) is not OutputFormat.TEXT:
        raise typer.BadParameter("--follow currently supports text output only")
    root = owner.instance(mode, launch_id, instance).root / "logs"
    files = (
        sorted(path for path in root.rglob("*") if path.is_file())
        if root.is_dir()
        else []
    )
    payload = {
        "path": str(root),
        "exists": root.exists(),
        "files": [str(path) for path in files],
    }
    if files:
        strategy_log = root / "strategy" / "process.log"
        latest = strategy_log if strategy_log.is_file() else files[-1]
        payload["latest"] = str(latest)
        content = latest.read_text(encoding="utf-8", errors="replace").splitlines()
        payload["lines"] = content[-lines:] if lines else []
    _emit(payload, output)
    if follow and files:
        position = latest.stat().st_size
        while True:
            try:
                with latest.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(position)
                    for line in stream:
                        typer.echo(line.rstrip("\n"), color=False)
                    position = stream.tell()
                time.sleep(0.25)
            except KeyboardInterrupt:
                return


@launch_app.command("artifacts", help="List files produced by a launch instance.")
def artifacts(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    root = owner.paths.launches / mode / launch_id / "instances" / resolved_instance
    _emit(
        {
            "path": str(root),
            "exists": root.exists(),
            "files": [str(path) for path in root.rglob("*")] if root.is_dir() else [],
        },
        output,
    )


@replay_app.command("events")
def replay_events(
    file: Path = typer.Option(..., "--file"),
    limit: int | None = typer.Option(None, "--limit"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(read_replay_events(file, limit=limit), output)
