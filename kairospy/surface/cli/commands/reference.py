from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer

from kairospy.application.reference import ReferenceCliApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli.options import OutputFormat, render


reference_app = typer.Typer(no_args_is_help=True, help="Reference and catalog commands")


def _legacy_group(name: str, commands: tuple[str, ...]) -> typer.Typer:
    group = typer.Typer(no_args_is_help=True, help=f"Legacy catalog {name} commands")
    reference_app.add_typer(group, name=name)
    del commands
    return group


assets_app = _legacy_group("assets", ("add", "list", "browse", "show"))
participants_app = _legacy_group("participants", ("brokers", "exchanges", "providers"))
sync_app = _legacy_group("sync", ("binance", "massive", "hyperliquid"))
events_app = _legacy_group("events", ())
markets_app = _legacy_group("markets", ("list", "browse", "resolve"))


@events_app.callback(invoke_without_command=True)
def events_callback(
    ctx: typer.Context,
    workspace: Path | None = typer.Option(None, "--workspace"),
    limit: int | None = typer.Option(None, "--limit"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    if ctx.invoked_subcommand is None:
        command = ["events"]
        if limit is not None:
            command.extend(("--limit", str(limit)))
        typer.echo(render(_invoke(workspace, command), output))
def _invoke(workspace: Path | None, command: list[str]) -> object:
    return ReferenceCliApplication(WorkspaceApplication().resolve(workspace)).run(command)


def _run(workspace: Path | None, output: OutputFormat, action: str) -> None:
    value = _invoke(workspace, [action])
    typer.echo(render(value, output))


@reference_app.command("status")
def status(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _run(workspace, output, "status")


@reference_app.command("sync")
def sync(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _run(workspace, output, "refresh")


@reference_app.command("publish")
def publish(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _run(workspace, output, "publish")


def _market_query(resolve: bool):
    def command(
        workspace: Path | None = typer.Option(None, "--workspace"),
        symbol: str | None = typer.Option(None, "--symbol"),
        market_id: str | None = typer.Option(None, "--market-id"),
        venue_id: str | None = typer.Option(None, "--venue-id"),
        venue: str | None = typer.Option(None, "--venue"),
        market: str | None = typer.Option(None, "--market"),
        status: str | None = typer.Option(None, "--status"),
        active_only: bool = typer.Option(False, "--active-only"),
        as_of_unix_nanos: int | None = typer.Option(None, "--as-of-unix-nanos"),
        limit: int | None = typer.Option(None, "--limit"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        command = ["markets", "resolve" if resolve else "list"]
        venue_id = venue_id or venue
        market_type = market or None
        for name, value in (("--symbol", symbol), ("--market-id", market_id), ("--venue-id", venue_id), ("--market-type", market_type), ("--status", status), ("--as-of-unix-nanos", str(as_of_unix_nanos) if as_of_unix_nanos is not None else None), ("--limit", str(limit) if limit is not None else None)):
            if value is not None:
                command.extend((name, value))
        if active_only and not resolve:
            command.append("--active-only")
        value = _invoke(workspace, command)
        typer.echo(render(value, output))

    command.__name__ = "resolve_market" if resolve else "list_markets"
    return command


markets_app.command("list")(_market_query(False))
markets_app.command("browse")(_market_query(False))
markets_app.command("resolve")(_market_query(True))


@assets_app.command("add")
def add_asset(
    asset_id: str = typer.Option(..., "--asset-id"),
    code: str = typer.Option(..., "--code"),
    asset_class: str = typer.Option("currency", "--asset-class"),
    name: str | None = typer.Option(None, "--name"),
    status: str = typer.Option("active", "--status"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    command = ["assets", "add", "--asset-id", asset_id, "--code", code,
               "--asset-class", asset_class, "--status", status]
    if name is not None:
        command.extend(("--name", name))
    value = _invoke(workspace, command)
    typer.echo(render(value, output))


@assets_app.command("list")
@assets_app.command("browse")
def list_assets(
    query: str | None = typer.Option(None, "--query"),
    status: str | None = typer.Option(None, "--status"),
    active_only: bool = typer.Option(False, "--active-only"),
    limit: int | None = typer.Option(None, "--limit"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    command = ["assets", "list"]
    for name, value in (("--query", query), ("--status", status), ("--limit", str(limit) if limit is not None else None)):
        if value is not None:
            command.extend((name, value))
    if active_only:
        command.append("--active-only")
    typer.echo(render(_invoke(workspace, command), output))


def _reference_refresh_action(action: str):
    def command(
        workspace: Path | None = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        provider = {"binance": "binance-spot", "massive": "massive-equity"}.get(action, action)
        value = _invoke(workspace, ["--provider", provider, "refresh"])
        if isinstance(value, dict):
            value["provider"] = action
        typer.echo(render(value, output))
    command.__name__ = f"reference_sync_{action}"
    return command


for _provider in ("binance", "massive", "hyperliquid"):
    sync_app.command(_provider)(_reference_refresh_action(_provider))


def _catalog_collection(collection: str):
    def command(
        workspace: Path | None = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        snapshot = _invoke(workspace, ["snapshot"])
        catalog = snapshot.get("catalog", {}) if isinstance(snapshot, dict) else {}
        value = catalog.get(collection, [])
        typer.echo(render(value, output))

    command.__name__ = f"catalog_{collection}"
    return command


@assets_app.command("show")
def show_asset(
    asset_id: str,
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    value = _invoke(workspace, ["assets", "show", asset_id])
    typer.echo(render(value, output))


def _participant_command(command: str):
    def invoke(
        workspace: Path | None = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        typer.echo(render(_invoke(workspace, ["participants", command]), output))

    invoke.__name__ = command
    return invoke


for _participant in ("brokers", "exchanges", "providers"):
    participants_app.command(_participant)(_participant_command(_participant))


def _reference_query(kind: str | None = None):
    def command(
        text_argument: str | None = typer.Argument(None),
        text: str | None = typer.Option(None, "--text"),
        kind_option: str | None = typer.Option(None, "--kind"),
        venue_id: str | None = typer.Option(None, "--venue-id", "--venue"),
        market_type: str | None = typer.Option(None, "--market-type", "--market"),
        status: str | None = typer.Option(None, "--status"),
        active_only: bool = typer.Option(False, "--active-only"),
        as_of_unix_nanos: int | None = typer.Option(None, "--as-of-unix-nanos"),
        limit: int | None = typer.Option(None, "--limit"),
        workspace: Path | None = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        args = []
        text = text or text_argument
        if text is not None:
            args.extend(("--text", text))
        selected_kind = kind or kind_option
        if selected_kind is not None:
            args.extend(("--kind", selected_kind))
        for name, value in (("--venue-id", venue_id), ("--market-type", market_type), ("--status", status), ("--as-of-unix-nanos", str(as_of_unix_nanos) if as_of_unix_nanos is not None else None), ("--limit", str(limit) if limit is not None else None)):
            if value is not None:
                args.extend((name, value))
        if active_only:
            args.append("--active-only")
        typer.echo(render(_invoke(workspace, ["query", *args]), output))
    command.__name__ = f"query_{kind or 'all'}"
    return command


reference_app.command("query")(_reference_query())
for _kind in ("entity", "asset", "instrument", "listing", "market", "event"):
    reference_app.command(f"query-{_kind}")(_reference_query(_kind))


@reference_app.command("search")
def search(
    text: str,
    limit: int = typer.Option(50, "--limit"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    typer.echo(render(_invoke(workspace, ["search", text, "--limit", str(limit)]), output))


@reference_app.command("show")
def show(
    identifier: str,
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    typer.echo(render(_invoke(workspace, ["show", identifier]), output))


@events_app.command("list")
def list_events(
    text: str | None = typer.Option(None, "--text"),
    venue_id: str | None = typer.Option(None, "--venue-id", "--venue"),
    status: str | None = typer.Option(None, "--status"),
    active_only: bool = typer.Option(False, "--active-only"),
    limit: int | None = typer.Option(None, "--limit"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    command = ["events"]
    for name, value in (("--text", text), ("--venue-id", venue_id), ("--status", status), ("--limit", str(limit) if limit is not None else None)):
        if value is not None:
            command.extend((name, value))
    if active_only:
        command.append("--active-only")
    typer.echo(render(_invoke(workspace, command), output))


@events_app.command("sync")
def sync_events(
    ticker: str = typer.Option(..., "--ticker"),
    venue_id: str | None = typer.Option(None, "--venue-id", "--venue"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    limit: int | None = typer.Option(None, "--limit"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    command = ["events", "sync", "--ticker", ticker]
    for name, value in (("--venue-id", venue_id), ("--start-unix-nanos", _unix_nanos(start)), ("--end-unix-nanos", _unix_nanos(end)), ("--limit", str(limit) if limit is not None else None)):
        if value is not None:
            command.extend((name, value))
    typer.echo(render(_invoke(workspace, command), output))


def _unix_nanos(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise typer.BadParameter(f"invalid ISO timestamp: {value}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return str(int(parsed.timestamp() * 1_000_000_000))


@reference_app.command("view")
def catalog_snapshot(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    typer.echo(render(_invoke(workspace, ["snapshot"]), output))
