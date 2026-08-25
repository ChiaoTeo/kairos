"""System lifecycle, diagnostics, status, and log commands."""

from __future__ import annotations

from pathlib import Path
import time

import typer

from kairospy.surface.cli.options import OutputFormat, effective_output
from kairospy.system.apps.components.application import (
    ComponentProcessApplication,
    SystemRuntimeSupervisor,
)
from kairospy.system.apps.components.application.process_logging import (
    current_run_id,
    decode_log_event,
    filter_log_lines,
    parse_since,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import (
    _emit,
    _ensure_no_active_component_dependents,
    system_app,
    system_component_app,
)


@system_app.command("inspect")
def system_inspect(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status(component), output)


@system_app.command("attach")
def system_attach(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(
        {"component": component, "socket": str(owner.paths.process_socket(component))},
        output,
    )


@system_app.command("command")
def system_command(
    component: str = typer.Option(..., "--component"),
    command: str = typer.Option(..., "--command"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    control = ComponentProcessApplication(owner).ensure_running("control")
    _emit(control.command(component, {"type": command}), output)


@system_app.command("up")
def system_up(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system up manages only workspace services: reference and market; "
            "launch starts instance-owned components"
        )
    owner = WorkspaceApplication().open(workspace)
    process = ComponentProcessApplication(owner)
    control = process.ensure_running(
        component,
        account_id=account_id,
        stream_startup_logs=component == "reference"
        and effective_output(output) is OutputFormat.TEXT,
    )
    supervisor = SystemRuntimeSupervisor(process)
    supervisor.register(component, {"account_id": account_id} if account_id else {})
    supervisor.start_background()
    _emit(control.status(), output)


@system_app.command("down")
def system_down(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system down manages only workspace services: reference and market; "
            "use launch stop for instance-owned components"
        )
    owner = WorkspaceApplication().open(workspace)
    _ensure_no_active_component_dependents(owner, component, "down")
    process = ComponentProcessApplication(owner)
    supervisor = SystemRuntimeSupervisor(process)
    supervisor.unregister(component)
    _emit(process.stop(component), output)


@system_app.command("restart")
def system_restart(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system restart manages only workspace services: reference and market; "
            "use launch start/stop for instance-owned components"
        )
    owner = WorkspaceApplication().open(workspace)
    _ensure_no_active_component_dependents(owner, component, "restart")
    process = ComponentProcessApplication(owner)
    text_output = effective_output(output) is OutputFormat.TEXT
    control = process.restart(
        component,
        account_id=account_id,
        stream_startup_logs=component == "reference" and text_output,
        progress=typer.echo if text_output else None,
    )
    supervisor = SystemRuntimeSupervisor(process)
    supervisor.register(component, {"account_id": account_id} if account_id else {})
    supervisor.start_background()
    _emit(control.status(), output)


@system_app.command("status")
def system_status(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status(component), output)


@system_component_app.command("status")
def system_component_status(
    component: str = typer.Argument(..., help="Workspace-scoped component name."),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect one workspace-scoped component server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status(component), output)


@system_app.command("list")
def system_list(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """List workspace-scoped system components without starting them."""
    output = effective_output(output)
    owner = WorkspaceApplication().open(workspace)
    value = ComponentProcessApplication(owner).list_status()
    if output is OutputFormat.JSON:
        _emit(value, output)
        return
    _emit(
        [
            {
                "component": component,
                "status": status.get("status", "unknown"),
                "pid": status.get("pid", ""),
                "pid_alive": status.get("pid_alive", ""),
                "control_socket": status.get("control_socket", ""),
                "log_file": status.get("log_file", ""),
            }
            for component, status in value.items()
        ],
        OutputFormat.TABLE,
    )


@system_app.command("logs")
def system_logs(
    component_argument: str | None = typer.Argument(
        None,
        metavar="COMPONENT",
        help="Component name (legacy positional form).",
    ),
    component: str | None = typer.Option(
        None,
        "--component",
        help="Component name, for example account or execution.",
    ),
    lines: int = typer.Option(
        100, "--lines", min=0, help="Number of recent lines to show."
    ),
    follow: bool = typer.Option(
        False, "-f", "--follow", help="Continue printing new output."
    ),
    current_run: bool = typer.Option(
        False, "--current-run", help="Show only the most recent process run."
    ),
    since: str | None = typer.Option(
        None, "--since", help="Show events since a duration (10m) or RFC3339 timestamp."
    ),
    level: str | None = typer.Option(None, "--level", help="Filter by log level."),
    event_name: str | None = typer.Option(
        None, "--event", help="Filter by structured event name."
    ),
    provider: str | None = typer.Option(
        None, "--provider", help="Filter by provider field."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Show a component's combined stdout/stderr log."""
    if (
        component is not None
        and component_argument is not None
        and component != component_argument
    ):
        raise typer.BadParameter(
            "component was specified twice with different values; "
            "use --component COMPONENT"
        )
    component = component or component_argument
    if component is None:
        raise typer.BadParameter("component is required; use --component COMPONENT")
    components = {
        "reference",
        "market",
        "account",
        "risk",
        "execution",
        "aeron",
        "system-supervisor",
    }
    if component not in components:
        raise typer.BadParameter(f"unsupported component: {component}")
    if follow and effective_output(output) is not OutputFormat.TEXT:
        raise typer.BadParameter("--follow currently supports text output only")
    owner = WorkspaceApplication().open(workspace)
    path = owner.paths.logs / component / "process.log"
    try:
        since_time = parse_since(since)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--since") from error
    content = (
        path.read_text(encoding="utf-8", errors="replace").splitlines()
        if path.is_file()
        else []
    )
    selected_run_id = current_run_id(content) if current_run else None
    filtered = filter_log_lines(
        content,
        level=level,
        event_name=event_name,
        provider=provider,
        since=since_time,
        run_id=selected_run_id,
    )
    visible = filtered[-lines:] if lines else []
    if effective_output(output) is OutputFormat.JSON:
        value = {
            "component": component,
            "path": str(path),
            "exists": path.is_file(),
            "run_id": selected_run_id,
            "lines": visible,
        }
        _emit(value, output)
        return
    if path.is_file() and visible:
        typer.echo("\n".join(visible))
    elif not path.is_file():
        typer.echo(f"log file does not exist: {path}")
    if not follow:
        return
    position = path.stat().st_size if path.is_file() else 0
    inode = path.stat().st_ino if path.is_file() else None
    try:
        while True:
            if path.is_file():
                current_stat = path.stat()
                if inode != current_stat.st_ino or current_stat.st_size < position:
                    position = 0
                    inode = current_stat.st_ino
                    selected_run_id = None
                with path.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(position)
                    for line in stream:
                        rendered = line.rstrip("\n")
                        value = decode_log_event(rendered)
                        if (
                            current_run
                            and value is not None
                            and value.get("event") == "process_spawned"
                        ):
                            selected_run_id = value.get("run_id")
                        if filter_log_lines(
                            [rendered],
                            level=level,
                            event_name=event_name,
                            provider=provider,
                            since=since_time,
                            run_id=selected_run_id if current_run else None,
                        ):
                            typer.echo(rendered, color=False)
                    position = stream.tell()
            time.sleep(0.25)
    except KeyboardInterrupt:
        return


@system_app.command("doctor")
def system_doctor(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Diagnose sockets, health files, locks, and unresponsive components."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).doctor(), output)


@system_app.command("repair")
def system_repair(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Remove only confirmed stale runtime resources."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).repair(), output)


@system_app.command("supervise")
def system_supervise(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    interval: float = typer.Option(1.0, "--interval", min=0.1),
    once: bool = typer.Option(False, "--once"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Run the workspace runtime reconciler for one desired component."""
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system supervise manages only workspace services: reference and market; "
            "launch owns instance components"
        )
    owner = WorkspaceApplication().open(workspace)
    desired: dict[str, object] = {}
    if component == "account" and account_id:
        desired["account_id"] = account_id
    supervisor = SystemRuntimeSupervisor(
        ComponentProcessApplication(owner),
        desired={component: desired},
    )
    value = supervisor.reconcile_once()
    if once:
        _emit(value[component], output)
        return
    supervisor.run_forever(interval=interval)
