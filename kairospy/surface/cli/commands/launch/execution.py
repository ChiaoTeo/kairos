"""Launch-instance Execution component commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.launch.application import LaunchRuntimeApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import instance_component_execution_app
from .support import (
    _EXECUTION_PASSTHROUGH_CONTEXT,
    _emit,
    _execution_connected_passthrough,
    _resolve_launch_target,
)


@instance_component_execution_app.command("status")
def launch_instance_component_execution_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the Execution component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, resolved_mode = _resolve_launch_target(
        owner, launch_id, mode, instance
    )
    instance_workspace = owner.instance(resolved_mode, launch_id, resolved_instance)
    statuses = LaunchRuntimeApplication(owner).component_status(instance_workspace)
    if "execution" not in statuses:
        raise typer.BadParameter("launch instance has no connected execution component")
    _emit(
        {
            **statuses["execution"],
            "owner": "execution",
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": resolved_mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_execution_app.command(
    "snapshot", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_snapshot(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read the launch-scoped Execution runtime snapshot."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "snapshot"
    )


@instance_component_execution_app.command(
    "routes", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_routes(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Execution route candidates."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "routes"
    )


@instance_component_execution_app.command(
    "active-orders", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_active_orders(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """List operational launch-scoped Execution orders."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "active-orders"
    )


@instance_component_execution_app.command(
    "recent-fills", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_recent_fills(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read the bounded recent Execution fill window."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "recent-fills"
    )


@instance_component_execution_app.command(
    "recent-order-events", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_recent_order_events(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read the bounded recent Execution order-event window."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "recent-order-events"
    )


@instance_component_execution_app.command(
    "audit", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_audit(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Execution audit records."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "audit"
    )


@instance_component_execution_app.command(
    "active-order", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_active_order(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Inspect one operational launch-scoped Execution order."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "active-order"
    )


@instance_component_execution_app.command(
    "reconcile", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_reconcile(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Request launch-scoped Execution reconciliation."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "reconcile"
    )


@instance_component_execution_app.command(
    "unknown-remote-orders", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_unknown_remote_orders(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """List launch-scoped unknown remote Execution orders."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "unknown-remote-orders"
    )


@instance_component_execution_app.command(
    "submit", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_submit(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Submit an order through the launch-scoped Execution runtime."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "submit"
    )


@instance_component_execution_app.command(
    "cancel", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_cancel(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Cancel an order through the launch-scoped Execution runtime."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "cancel"
    )


@instance_component_execution_app.command(
    "replace", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_replace(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Replace an order through the launch-scoped Execution runtime."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "replace"
    )
