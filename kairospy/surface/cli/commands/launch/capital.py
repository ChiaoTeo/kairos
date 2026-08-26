"""Launch-instance Capital component commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import instance_component_capital_app
from .support import (
    _emit,
    _instance_capital_client,
    _launch_instance_component_named_status,
    _run_capital_connected_command,
)


@instance_component_capital_app.command("status")
def launch_instance_component_capital_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the Capital component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    value, resolved_instance, mode = _launch_instance_component_named_status(
        owner, launch_id, "capital", instance
    )
    _emit(
        {
            **value,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("health")
def launch_instance_component_capital_health(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital health through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.health(),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("current")
def launch_instance_component_capital_current(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital current-view business facts."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_metadata(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("availabilities")
def launch_instance_component_capital_availabilities(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital availability facts from the indexed view."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_availabilities(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("objectives")
def launch_instance_component_capital_objectives(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital funding objectives from the indexed view."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_objectives(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("demands")
def launch_instance_component_capital_demands(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital demands from the indexed view."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_demands(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("plans")
def launch_instance_component_capital_plans(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital plans from the indexed view."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_plans(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("routes")
def launch_instance_component_capital_routes(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital routes from the indexed view."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_routes(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("reservations")
def launch_instance_component_capital_reservations(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital reservations from the indexed view."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_reservations(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("operations")
def launch_instance_component_capital_operations(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital operations from the indexed view."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_operations(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("alerts")
def launch_instance_component_capital_alerts(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital recovery alerts from the indexed view."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_alerts(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("publish-funding-objective")
def launch_instance_component_capital_publish_funding_objective(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Publish a launch-scoped Capital funding objective."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_capital_connected_command(
            owner,
            launch_id,
            instance,
            "publish-funding-objective",
            ["--file", str(file)],
        ),
        output,
    )


@instance_component_capital_app.command("observe-demand")
def launch_instance_component_capital_observe_demand(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Observe a launch-scoped Capital demand."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_capital_connected_command(
            owner, launch_id, instance, "observe-demand", ["--file", str(file)]
        ),
        output,
    )


@instance_component_capital_app.command("cancel-funding-objective")
def launch_instance_component_capital_cancel_funding_objective(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Cancel a launch-scoped Capital funding objective."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_capital_connected_command(
            owner,
            launch_id,
            instance,
            "cancel-funding-objective",
            ["--file", str(file)],
        ),
        output,
    )


@instance_component_capital_app.command("reconcile-plan")
def launch_instance_component_capital_reconcile_plan(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Reconcile a launch-scoped Capital plan."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_capital_connected_command(
            owner, launch_id, instance, "reconcile-plan", ["--file", str(file)]
        ),
        output,
    )
