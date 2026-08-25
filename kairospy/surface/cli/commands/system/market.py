"""Workspace-scoped Market component commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import (
    _component_dependents,
    _emit,
    _run_workspace_market_connected_command,
    system_component_market_app,
)


@system_component_market_app.command("status")
def system_component_market_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the workspace-scoped Market component server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        {**ComponentProcessApplication(owner).status("market"), "scope": "system"},
        output,
    )


@system_component_market_app.command("routes")
def system_component_market_routes(
    workspace: Path = typer.Option(None, "--workspace"),
    market_id: str | None = typer.Option(None, "--market-id"),
    instrument_id: str | None = typer.Option(None, "--instrument-id"),
    observation_kind: str | None = typer.Option(None, "--observation-kind"),
    provider: str | None = typer.Option(None, "--provider"),
    configured_only: bool = typer.Option(False, "--configured-only"),
    ready_only: bool = typer.Option(False, "--ready-only"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read provider-route readiness from the workspace-scoped Market server."""
    owner = WorkspaceApplication().open(workspace)
    arguments: list[str] = []
    for option, value in (
        ("--market-id", market_id),
        ("--instrument-id", instrument_id),
        ("--observation-kind", observation_kind),
        ("--provider", provider),
    ):
        if value is not None:
            arguments.extend((option, value))
    if configured_only:
        arguments.append("--configured-only")
    if ready_only:
        arguments.append("--ready-only")
    _emit(_run_workspace_market_connected_command(owner, "routes", arguments), output)


@system_component_market_app.command("snapshot")
def system_component_market_snapshot(
    kind: str = typer.Argument(..., help="Snapshot kind: quote, bar, or greeks."),
    market_id: str | None = typer.Option(None, "--market-id"),
    provider: str | None = typer.Option(None, "--provider"),
    symbol: str | None = typer.Option(None, "--symbol"),
    exchange: str = typer.Option("binance", "--exchange"),
    market_type: str = typer.Option("spot", "--market-type"),
    timeframe: str | None = typer.Option(None, "--timeframe"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read one current Market current view from the workspace scope."""
    owner = WorkspaceApplication().open(workspace)
    if market_id is None:
        if not symbol:
            raise typer.BadParameter("snapshot requires --market-id or --symbol")
        market_id = f"market:{exchange.lower()}:{market_type.lower()}:{symbol.upper()}"
    arguments = [kind, "--market-id", market_id]
    if provider is not None:
        arguments.extend(("--provider", provider))
    if timeframe is not None:
        arguments.extend(("--timeframe", timeframe))
    _emit(_run_workspace_market_connected_command(owner, "snapshot", arguments), output)


@system_component_market_app.command("freshness")
def system_component_market_freshness(
    market_id: str = typer.Option(..., "--market-id"),
    observation: str | None = typer.Option(None, "--observation"),
    provider: str | None = typer.Option(None, "--provider"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read one current Market freshness current view from the workspace scope."""
    owner = WorkspaceApplication().open(workspace)
    arguments = ["--market-id", market_id]
    if observation is not None:
        arguments.extend(["--observation", observation])
    if provider is not None:
        arguments.extend(["--provider", provider])
    _emit(
        _run_workspace_market_connected_command(owner, "freshness", arguments), output
    )


@system_component_market_app.command("subscribe")
def system_component_market_subscribe(
    subscription_id: str = typer.Option(..., "--subscription-id"),
    market_id: str = typer.Option(..., "--market-id"),
    strategy_id: str = typer.Option("cli", "--strategy-id"),
    instance_id: str = typer.Option("cli", "--instance-id"),
    data: list[str] = typer.Option(..., "--data"),
    prefer_provider: list[str] = typer.Option([], "--prefer-provider"),
    require_provider: list[str] = typer.Option([], "--require-provider"),
    all_eligible_providers: bool = typer.Option(False, "--all-eligible-providers"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Create a runtime subscription on the workspace-scoped Market server."""
    policies = bool(prefer_provider) + bool(require_provider) + all_eligible_providers
    if policies > 1:
        raise typer.BadParameter("select only one provider policy")
    arguments = [
        "--subscription-id",
        subscription_id,
        "--market-id",
        market_id,
        "--strategy-id",
        strategy_id,
        "--instance-id",
        instance_id,
    ]
    for value in data:
        arguments.extend(["--data", value])
    for value in prefer_provider:
        arguments.extend(["--prefer-provider", value])
    for value in require_provider:
        arguments.extend(["--require-provider", value])
    if all_eligible_providers:
        arguments.append("--all-eligible-providers")
    owner = WorkspaceApplication().open(workspace)
    value = _run_workspace_market_connected_command(owner, "subscribe", arguments)
    _emit(value, output)


@system_component_market_app.command("unsubscribe")
def system_component_market_unsubscribe(
    subscription_id: str = typer.Option(..., "--subscription-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Remove a runtime subscription from the workspace-scoped Market server."""
    owner = WorkspaceApplication().open(workspace)
    value = _run_workspace_market_connected_command(
        owner, "unsubscribe", ["--subscription-id", subscription_id]
    )
    _emit(value, output)


@system_component_market_app.command("recover")
def system_component_market_recover(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Request bounded source recovery on the workspace-scoped Market server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_run_workspace_market_connected_command(owner, "recover", []), output)


@system_component_market_app.command("pause-replay")
def system_component_market_pause_replay(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Pause Market replay input on the workspace-scoped Market server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_run_workspace_market_connected_command(owner, "pause-replay", []), output)


@system_component_market_app.command("resume-replay")
def system_component_market_resume_replay(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Resume Market replay input on the workspace-scoped Market server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_run_workspace_market_connected_command(owner, "resume-replay", []), output)


@system_component_market_app.command("dependents")
def system_component_market_dependents(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """List running launch instances that use the workspace-scoped Market."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        {
            "component": "market",
            "scope": "workspace",
            "dependents": _component_dependents(owner, "market"),
        },
        output,
    )
