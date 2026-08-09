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
        entities_snapshot_path=owner.paths.reference_snapshot("entities"),
        assets_snapshot_path=owner.paths.reference_snapshot("assets"),
        instruments_snapshot_path=owner.paths.reference_snapshot("instruments"),
        listings_snapshot_path=owner.paths.reference_snapshot("listings"),
        markets_snapshot_path=owner.paths.reference_snapshot("markets"),
        financial_products_snapshot_path=owner.paths.reference_snapshot("financial-products"),
        execution_accesses_snapshot_path=owner.paths.reference_snapshot("execution-accesses"),
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


@reference_app.command("catalog")
def reference_catalog(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    """Read the complete catalog projection, including all collections."""
    from kairospy.surface.cli.options import OutputFormat, render
    typer.echo(render(_client(workspace).catalog(), OutputFormat(output)))


@reference_app.command("snapshots")
def reference_snapshots(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("table", "--output", "--format"),
) -> None:
    """List Reference snapshot views, identities, and resource paths."""
    from kairospy.surface.cli.options import OutputFormat, render
    typer.echo(render(_client(workspace).snapshot_views(), OutputFormat(output)))


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


@reference_app.command("lifecycle")
def reference_lifecycle(
    limit: int | None = typer.Option(None, "--limit", min=1),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("text", "--output", "--format"),
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render
    typer.echo(render(_client(workspace).lifecycle(limit=limit), OutputFormat(output)))


def _reference_collection_command(view: str, workspace: Path | None, output: str) -> None:
    from kairospy.surface.cli.options import OutputFormat, render
    typer.echo(render(_client(workspace).collection(view), OutputFormat(output)))


@reference_app.command("assets")
def reference_assets(workspace: Path | None = typer.Option(None, "--workspace"), output: str = typer.Option("table", "--output", "--format")) -> None:
    _reference_collection_command("assets", workspace, output)


@reference_app.command("entities")
def reference_entities(workspace: Path | None = typer.Option(None, "--workspace"), output: str = typer.Option("table", "--output", "--format")) -> None:
    _reference_collection_command("entities", workspace, output)


@reference_app.command("instruments")
def reference_instruments(workspace: Path | None = typer.Option(None, "--workspace"), output: str = typer.Option("table", "--output", "--format")) -> None:
    _reference_collection_command("instruments", workspace, output)


@reference_app.command("listings")
def reference_listings(workspace: Path | None = typer.Option(None, "--workspace"), output: str = typer.Option("table", "--output", "--format")) -> None:
    _reference_collection_command("listings", workspace, output)


@reference_app.command("financial-products")
def reference_financial_products(workspace: Path | None = typer.Option(None, "--workspace"), output: str = typer.Option("table", "--output", "--format")) -> None:
    _reference_collection_command("financial-products", workspace, output)


@reference_app.command("execution-accesses")
def reference_execution_accesses(workspace: Path | None = typer.Option(None, "--workspace"), output: str = typer.Option("table", "--output", "--format")) -> None:
    _reference_collection_command("execution-accesses", workspace, output)


__all__ = ["reference_app"]
