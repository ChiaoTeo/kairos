"""Workspace-scoped Capital component commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import (
    _emit,
    _run_workspace_capital_connected_command,
    _workspace_capital_client,
    system_component_capital_app,
)


@system_component_capital_app.command("status")
def system_component_capital_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the workspace-scoped Capital component server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status("capital"), output)


@system_component_capital_app.command("health")
def system_component_capital_health(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Capital runtime health through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_capital_client(owner).health(), output)


@system_component_capital_app.command("current")
def system_component_capital_current(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital current-view business facts from the workspace component."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_metadata(capital_group_id),
        output,
    )


@system_component_capital_app.command("availabilities")
def system_component_capital_availabilities(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital availability facts from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_availabilities(capital_group_id),
        output,
    )


@system_component_capital_app.command("objectives")
def system_component_capital_objectives(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital funding objectives from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_objectives(capital_group_id),
        output,
    )


@system_component_capital_app.command("demands")
def system_component_capital_demands(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital demands from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_demands(capital_group_id),
        output,
    )


@system_component_capital_app.command("plans")
def system_component_capital_plans(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital plans from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_plans(capital_group_id),
        output,
    )


@system_component_capital_app.command("routes")
def system_component_capital_routes(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital routes from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_routes(capital_group_id),
        output,
    )


@system_component_capital_app.command("reservations")
def system_component_capital_reservations(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital reservations from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_reservations(capital_group_id),
        output,
    )


@system_component_capital_app.command("operations")
def system_component_capital_operations(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital operations from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_operations(capital_group_id),
        output,
    )


@system_component_capital_app.command("alerts")
def system_component_capital_alerts(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital recovery alerts from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_capital_client(owner).current_alerts(capital_group_id), output)


@system_component_capital_app.command("publish-funding-objective")
def system_component_capital_publish_funding_objective(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Publish a Capital runtime funding objective through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_capital_connected_command(
            owner, "publish-funding-objective", ["--file", str(file)]
        ),
        output,
    )


@system_component_capital_app.command("observe-demand")
def system_component_capital_observe_demand(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Observe a Capital runtime demand through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_capital_connected_command(
            owner, "observe-demand", ["--file", str(file)]
        ),
        output,
    )


@system_component_capital_app.command("cancel-funding-objective")
def system_component_capital_cancel_funding_objective(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Cancel a Capital runtime funding objective through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_capital_connected_command(
            owner, "cancel-funding-objective", ["--file", str(file)]
        ),
        output,
    )


@system_component_capital_app.command("reconcile-plan")
def system_component_capital_reconcile_plan(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Reconcile a Capital runtime plan through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_capital_connected_command(
            owner, "reconcile-plan", ["--file", str(file)]
        ),
        output,
    )
