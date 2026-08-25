"""Launch-instance Risk component commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import instance_component_risk_app
from .support import (
    _emit,
    _launch_instance_component_named_status,
    _resolve_launch_target,
    _risk_circuit_arguments,
    _run_risk_connected_command,
)


@instance_component_risk_app.command("status")
def launch_instance_component_risk_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the Risk component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    value, resolved_instance, mode = _launch_instance_component_named_status(
        owner, launch_id, "risk", instance
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


@instance_component_risk_app.command("health")
def launch_instance_component_risk_health(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read launch-scoped Risk health through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(owner, launch_id, instance, "health", []),
        output,
    )


@instance_component_risk_app.command("latest")
def launch_instance_component_risk_latest(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    actor_id: str | None = typer.Option(None, "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Risk latest-view business facts."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, _mode = _resolve_launch_target(owner, launch_id, None, instance)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "latest",
            ["--actor-id", actor_id or f"risk:{resolved_instance}"],
        ),
        output,
    )


@instance_component_risk_app.command("limits")
def launch_instance_component_risk_limits(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    actor_id: str | None = typer.Option(None, "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Risk limit usage resources."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, _mode = _resolve_launch_target(owner, launch_id, None, instance)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "limits",
            ["--actor-id", actor_id or f"risk:{resolved_instance}"],
        ),
        output,
    )


@instance_component_risk_app.command("reservations")
def launch_instance_component_risk_reservations(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    actor_id: str | None = typer.Option(None, "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Risk active reservations."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, _mode = _resolve_launch_target(owner, launch_id, None, instance)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "reservations",
            ["--actor-id", actor_id or f"risk:{resolved_instance}"],
        ),
        output,
    )


@instance_component_risk_app.command("circuits")
def launch_instance_component_risk_circuits(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    actor_id: str | None = typer.Option(None, "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Risk circuit states."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, _mode = _resolve_launch_target(owner, launch_id, None, instance)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "circuits",
            ["--actor-id", actor_id or f"risk:{resolved_instance}"],
        ),
        output,
    )


@instance_component_risk_app.command("pre-trade-check")
def launch_instance_component_risk_pre_trade_check(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Evaluate launch-scoped Risk authorization through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "pre-trade-check",
            ["--file", str(file)],
        ),
        output,
    )


@instance_component_risk_app.command("authorize-reserve")
def launch_instance_component_risk_authorize_reserve(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Authorize and reserve launch-scoped Risk budget through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "authorize-reserve",
            ["--file", str(file)],
        ),
        output,
    )


@instance_component_risk_app.command("release")
def launch_instance_component_risk_release(
    launch_id: str,
    reservation_id: str = typer.Option(..., "--reservation-id"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Release a launch-scoped Risk reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
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


@instance_component_risk_app.command("consume")
def launch_instance_component_risk_consume(
    launch_id: str,
    reservation_id: str = typer.Option(..., "--reservation-id"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Consume a launch-scoped Risk reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
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


@instance_component_risk_app.command("resize")
def launch_instance_component_risk_resize(
    launch_id: str,
    reservation_id: str = typer.Option(..., "--reservation-id"),
    amount: str = typer.Option(..., "--amount"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Resize a launch-scoped Risk reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
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


@instance_component_risk_app.command("open-circuit")
def launch_instance_component_risk_open_circuit(
    launch_id: str,
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    reason: str = typer.Option(..., "--reason"),
    reset_at_unix_nanos: int | None = typer.Option(None, "--reset-at-unix-nanos"),
    account_id: str | None = typer.Option(None, "--account-id"),
    strategy_id: str | None = typer.Option(None, "--strategy-id"),
    exchange_id: str | None = typer.Option(None, "--exchange-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Open a launch-scoped Risk circuit through its owner contract."""
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
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "open-circuit",
            arguments,
        ),
        output,
    )


@instance_component_risk_app.command("close-circuit")
def launch_instance_component_risk_close_circuit(
    launch_id: str,
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    account_id: str | None = typer.Option(None, "--account-id"),
    strategy_id: str | None = typer.Option(None, "--strategy-id"),
    exchange_id: str | None = typer.Option(None, "--exchange-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Close a launch-scoped Risk circuit through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
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


@instance_component_risk_app.command("publish-policy")
def launch_instance_component_risk_publish_policy(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Publish a launch-scoped Risk policy through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "publish-policy",
            ["--file", str(file)],
        ),
        output,
    )


@instance_component_risk_app.command("advance-time")
def launch_instance_component_risk_advance_time(
    launch_id: str,
    event_time_unix_nanos: int = typer.Option(..., "--event-time-unix-nanos"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Advance launch-scoped Risk runtime time through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "advance-time",
            ["--event-time-unix-nanos", str(event_time_unix_nanos)],
        ),
        output,
    )
