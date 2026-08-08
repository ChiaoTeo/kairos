"""KairosPy commands for the running Reference process."""

from __future__ import annotations

from pathlib import Path
import typer

from kairospy.application.reference import ReferenceSnapshotClient
from kairospy.application.workspace import WorkspaceApplication

reference_app = typer.Typer(no_args_is_help=True, help="Query the running Reference process")


def _client(workspace: Path | None) -> ReferenceSnapshotClient:
    owner = WorkspaceApplication().open(workspace)
    return ReferenceSnapshotClient(
        socket_path=owner.paths.reference_socket(),
        snapshot_path=owner.paths.reference_snapshot("catalog"),
        markets_snapshot_path=owner.paths.reference_snapshot("markets"),
    )


@reference_app.command("health")
def reference_health(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("text", "--output", "--format"),
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render
    typer.echo(render(_client(workspace).health(), OutputFormat(output)))


@reference_app.command("snapshot")
def reference_snapshot(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render
    typer.echo(render(_client(workspace).snapshot(), OutputFormat(output)))


@reference_app.command("providers")
def reference_providers(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render
    typer.echo(render(_client(workspace).providers(), OutputFormat(output)))


@reference_app.command("refresh")
def reference_refresh(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render
    typer.echo(render(_client(workspace).refresh(), OutputFormat(output)))


@reference_app.command("markets")
def reference_markets(
    symbol: str | None = typer.Option(None, "--symbol"),
    venue_id: str | None = typer.Option(None, "--venue-id", "--venue"),
    market_type: str | None = typer.Option(None, "--market-type"),
    asset_type: str | None = typer.Option(None, "--asset-type"),
    active_only: bool = typer.Option(False, "--active-only"),
    status: str | None = typer.Option(None, "--status"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("text", "--output", "--format"),
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render
    value = _client(workspace).markets(
        symbol=symbol,
        venue_id=venue_id,
        market_type=market_type,
        asset_type=asset_type,
        active_only=active_only,
        status=status,
    )
    typer.echo(render(value, OutputFormat(output)))


__all__ = ["reference_app"]
