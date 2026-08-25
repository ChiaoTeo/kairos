"""Launch-instance Market component commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.launch.application import LaunchRuntimeApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import instance_component_market_app
from .support import (
    _emit,
    _resolve_launch_target,
    _run_instance_market_connected_command,
)


@instance_component_market_app.command("status")
def launch_instance_component_market_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the Market component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    market = LaunchRuntimeApplication(owner).component_status(instance_workspace)[
        "market"
    ]
    _emit(
        {
            **market,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_market_app.command("routes")
def launch_instance_component_market_routes(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    market_id: str | None = typer.Option(None, "--market-id"),
    instrument_id: str | None = typer.Option(None, "--instrument-id"),
    observation_kind: str | None = typer.Option(None, "--observation-kind"),
    provider: str | None = typer.Option(None, "--provider"),
    configured_only: bool = typer.Option(False, "--configured-only"),
    ready_only: bool = typer.Option(False, "--ready-only"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Market provider-route readiness selected by a launch instance."""
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
    _emit(
        _run_instance_market_connected_command(
            owner,
            launch_id=launch_id,
            instance=instance,
            command="routes",
            arguments=arguments,
            require_views=False,
        ),
        output,
    )


@instance_component_market_app.command("snapshot")
def launch_instance_component_market_snapshot(
    launch_id: str,
    kind: str = typer.Argument(..., help="Snapshot kind: quote, bar, or greeks."),
    provider: str | None = typer.Option(None, "--provider"),
    market_id: str | None = typer.Option(None, "--market-id"),
    symbol: str | None = typer.Option(None, "--symbol"),
    exchange: str = typer.Option("binance", "--exchange"),
    market_type: str = typer.Option("spot", "--market-type"),
    timeframe: str | None = typer.Option(None, "--timeframe"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read one Market current view selected by a launch instance."""
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
    _emit(
        _run_instance_market_connected_command(
            owner,
            launch_id=launch_id,
            instance=instance,
            command="snapshot",
            arguments=arguments,
            require_views=True,
        ),
        output,
    )


@instance_component_market_app.command("freshness")
def launch_instance_component_market_freshness(
    launch_id: str,
    market_id: str = typer.Option(..., "--market-id"),
    observation: str | None = typer.Option(None, "--observation"),
    provider: str | None = typer.Option(None, "--provider"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Market freshness selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    arguments = ["--market-id", market_id]
    if observation is not None:
        arguments.extend(("--observation", observation))
    if provider is not None:
        arguments.extend(("--provider", provider))
    _emit(
        _run_instance_market_connected_command(
            owner,
            launch_id=launch_id,
            instance=instance,
            command="freshness",
            arguments=arguments,
            require_views=True,
        ),
        output,
    )


@instance_component_market_app.command("pause-replay")
def launch_instance_component_market_pause_replay(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Pause Market replay input selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_instance_market_connected_command(
            owner,
            launch_id=launch_id,
            instance=instance,
            command="pause-replay",
            arguments=[],
            require_views=False,
        ),
        output,
    )


@instance_component_market_app.command("resume-replay")
def launch_instance_component_market_resume_replay(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Resume Market replay input selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_instance_market_connected_command(
            owner,
            launch_id=launch_id,
            instance=instance,
            command="resume-replay",
            arguments=[],
            require_views=False,
        ),
        output,
    )
