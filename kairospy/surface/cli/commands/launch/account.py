"""Launch-instance Account component commands."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import instance_component_account_app
from .support import (
    _emit,
    _emit_launch_account_balances,
    _instance_account_client,
    _instance_account_snapshot,
    _run_account_connected_command,
)


@instance_component_account_app.command("snapshot")
def launch_instance_component_account_snapshot(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read one Account current view selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    snapshot, resolved_instance, mode = _instance_account_snapshot(
        owner, launch_id=launch_id, instance=instance, account_id=account_id
    )
    _emit(
        {
            **snapshot,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_account_app.command("balances")
def launch_instance_component_account_balances(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read balances from an Account component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    snapshot, resolved_instance, mode = _instance_account_snapshot(
        owner, launch_id=launch_id, instance=instance, account_id=account_id
    )
    balances = [
        balance
        for segment in snapshot["segments"]
        for balance in segment.get("balances", [])
    ]
    _emit_launch_account_balances(
        {
            "account_id": account_id,
            "balances": balances,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_account_app.command("positions")
def launch_instance_component_account_positions(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read positions from an Account component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    snapshot, resolved_instance, mode = _instance_account_snapshot(
        owner, launch_id=launch_id, instance=instance, account_id=account_id
    )
    positions = [
        position
        for segment in snapshot["segments"]
        for position in segment.get("positions", [])
    ]
    _emit(
        {
            "account_id": account_id,
            "positions": positions,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_account_app.command("open-orders")
def launch_instance_component_account_open_orders(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read observed orders from an Account component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_account_client(
        owner, launch_id=launch_id, instance=instance, account_id=account_id
    )
    from kairospy.primitives.account import AccountId

    account_key = AccountId(account_id)
    orders = tuple(client.observed_orders_view(account_key).snapshot().observed_orders)
    _emit(
        {
            "account_id": account_id,
            "open_orders": [_observed_order_payload(value) for value in orders],
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


def _observed_order_payload(value: object) -> dict[str, object]:
    return {
        "observation_id": getattr(value, "observation_id"),
        "source_id": getattr(value, "source_id"),
        "execution_order_id": getattr(value, "execution_order_id"),
        "remote_order_id": getattr(value, "remote_order_id"),
        "instrument_id": getattr(value, "instrument_id"),
        "market_id": getattr(value, "market_id"),
        "side": getattr(value, "side"),
        "quantity": _decimal_presentation(getattr(value, "quantity")),
        "filled_quantity": _decimal_presentation(getattr(value, "filled_quantity")),
        "status": getattr(value, "status"),
        "observed_at_unix_nanos": getattr(value, "observed_at_unix_nanos"),
        "segment_key": getattr(value, "segment_key"),
    }


def _decimal_presentation(value: object) -> Decimal:
    raw = getattr(value, "value", None)
    if not isinstance(raw, Decimal):
        raise TypeError("Account value must expose a Decimal value")
    return raw


@instance_component_account_app.command("refresh")
def launch_instance_component_account_refresh(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Request refresh on an Account component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_account_connected_command(
            owner, launch_id, instance, account_id, "refresh", []
        ),
        output,
    )


@instance_component_account_app.command("reconcile")
def launch_instance_component_account_reconcile(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Request reconciliation on an Account component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_account_connected_command(
            owner, launch_id, instance, account_id, "reconcile", []
        ),
        output,
    )
