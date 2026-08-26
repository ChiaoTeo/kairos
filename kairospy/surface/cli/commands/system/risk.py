"""Workspace-scoped Risk component commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import (
    _emit,
    _run_workspace_risk_connected_command,
    _workspace_risk_client,
    system_component_risk_app,
)


@system_component_risk_app.command("status")
def system_component_risk_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the workspace-scoped Risk component server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status("risk"), output)


@system_component_risk_app.command("health")
def system_component_risk_health(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Risk runtime health through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_risk_client(owner).health(), output)


@system_component_risk_app.command("latest")
def system_component_risk_latest(
    actor_id: str = typer.Option("risk", "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Risk latest-view business facts from the workspace component."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_risk_client(owner).latest_metadata(actor_id=actor_id), output)


@system_component_risk_app.command("limits")
def system_component_risk_limits(
    actor_id: str = typer.Option("risk", "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Risk limit usage resources from the workspace indexed view."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_risk_client(owner).latest_limits(actor_id=actor_id), output)


@system_component_risk_app.command("reservations")
def system_component_risk_reservations(
    actor_id: str = typer.Option("risk", "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Risk active reservations from the workspace indexed view."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_risk_client(owner).latest_reservations(actor_id=actor_id), output)


@system_component_risk_app.command("circuits")
def system_component_risk_circuits(
    actor_id: str = typer.Option("risk", "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Risk circuit states from the workspace indexed view."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_risk_client(owner).latest_circuits(actor_id=actor_id), output)


@system_component_risk_app.command("pre-trade-check")
def system_component_risk_pre_trade_check(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Evaluate a Risk runtime authorization request through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner, "pre-trade-check", ["--file", str(file)]
        ),
        output,
    )


@system_component_risk_app.command("authorize-reserve")
def system_component_risk_authorize_reserve(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Authorize and reserve Risk runtime budget through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner, "authorize-reserve", ["--file", str(file)]
        ),
        output,
    )


@system_component_risk_app.command("release")
def system_component_risk_release(
    reservation_id: str = typer.Option(..., "--reservation-id"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Release a Risk runtime reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner,
            "release",
            [
                "--reservation-id",
                reservation_id,
                "--at-unix-nanos",
                str(at_unix_nanos),
            ],
        ),
        output,
    )


@system_component_risk_app.command("consume")
def system_component_risk_consume(
    reservation_id: str = typer.Option(..., "--reservation-id"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Consume a Risk runtime reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner,
            "consume",
            [
                "--reservation-id",
                reservation_id,
                "--at-unix-nanos",
                str(at_unix_nanos),
            ],
        ),
        output,
    )


@system_component_risk_app.command("resize")
def system_component_risk_resize(
    reservation_id: str = typer.Option(..., "--reservation-id"),
    amount: str = typer.Option(..., "--amount"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Resize a Risk runtime reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner,
            "resize",
            [
                "--reservation-id",
                reservation_id,
                "--amount",
                amount,
                "--at-unix-nanos",
                str(at_unix_nanos),
            ],
        ),
        output,
    )


def _risk_circuit_arguments(
    *,
    account_id: str | None,
    strategy_id: str | None,
    exchange_id: str | None,
) -> list[str]:
    arguments: list[str] = []
    if account_id is not None:
        arguments.extend(["--account-id", account_id])
    if strategy_id is not None:
        arguments.extend(["--strategy-id", strategy_id])
    if exchange_id is not None:
        arguments.extend(["--exchange-id", exchange_id])
    return arguments


@system_component_risk_app.command("open-circuit")
def system_component_risk_open_circuit(
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    reason: str = typer.Option(..., "--reason"),
    reset_at_unix_nanos: int | None = typer.Option(None, "--reset-at-unix-nanos"),
    account_id: str | None = typer.Option(None, "--account-id"),
    strategy_id: str | None = typer.Option(None, "--strategy-id"),
    exchange_id: str | None = typer.Option(None, "--exchange-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Open a Risk runtime circuit through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    arguments = [
        "--at-unix-nanos",
        str(at_unix_nanos),
        "--reason",
        reason,
        *_risk_circuit_arguments(
            account_id=account_id,
            strategy_id=strategy_id,
            exchange_id=exchange_id,
        ),
    ]
    if reset_at_unix_nanos is not None:
        arguments.extend(["--reset-at-unix-nanos", str(reset_at_unix_nanos)])
    _emit(
        _run_workspace_risk_connected_command(owner, "open-circuit", arguments),
        output,
    )


@system_component_risk_app.command("close-circuit")
def system_component_risk_close_circuit(
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    account_id: str | None = typer.Option(None, "--account-id"),
    strategy_id: str | None = typer.Option(None, "--strategy-id"),
    exchange_id: str | None = typer.Option(None, "--exchange-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Close a Risk runtime circuit through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner,
            "close-circuit",
            [
                "--at-unix-nanos",
                str(at_unix_nanos),
                *_risk_circuit_arguments(
                    account_id=account_id,
                    strategy_id=strategy_id,
                    exchange_id=exchange_id,
                ),
            ],
        ),
        output,
    )


@system_component_risk_app.command("publish-policy")
def system_component_risk_publish_policy(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Publish a Risk runtime policy through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner, "publish-policy", ["--file", str(file)]
        ),
        output,
    )


@system_component_risk_app.command("advance-time")
def system_component_risk_advance_time(
    event_time_unix_nanos: int = typer.Option(..., "--event-time-unix-nanos"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Advance Risk runtime time through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner,
            "advance-time",
            ["--event-time-unix-nanos", str(event_time_unix_nanos)],
        ),
        output,
    )
