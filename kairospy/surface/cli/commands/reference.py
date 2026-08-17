"""KairosPy commands for the running Reference process."""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import Any
import typer

from kairospy.application.workspace import WorkspaceApplication
from kairospy.infrastructure.contracts.reference import ReferenceClient

reference_app = typer.Typer(
    no_args_is_help=True, help="Query the running Reference process"
)


def _client(workspace: Path | None) -> ReferenceClient:
    owner = WorkspaceApplication().open(workspace)
    return ReferenceClient(
        socket_path=owner.paths.reference_socket(),
        database_path=owner.paths.reference_database(),
    )


@reference_app.command("health")
def reference_health(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("text", "--output", "--format"),
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(render(_client(workspace).health(), OutputFormat(output)))


@reference_app.command("catalog")
def reference_catalog(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    """Read the complete catalog projection, including all collections."""
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(render(_client(workspace).catalog(), OutputFormat(output)))


@reference_app.command("providers")
def reference_providers(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(render(_client(workspace).providers(), OutputFormat(output)))


@reference_app.command("validate")
def reference_validate(
    require_massive: bool = typer.Option(False, "--require-massive"),
    allow_pending_publication: bool = typer.Option(
        False, "--allow-pending-publication"
    ),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    """Run the repeatable Reference runtime acceptance checks."""
    from kairospy.application.reference import (
        MASSIVE_REFERENCE_SOURCES,
        validate_reference_runtime,
    )
    from kairospy.surface.cli.options import OutputFormat, render

    client = _client(workspace)
    provider_rows = client.providers().get("providers", [])
    configured_sources = tuple(
        str(value["source_id"])
        for value in provider_rows
        if isinstance(value, dict) and value.get("source_id")
    )
    required_sources = configured_sources + (
        MASSIVE_REFERENCE_SOURCES if require_massive else ()
    )
    result = validate_reference_runtime(
        client,
        required_sources=required_sources,
        require_published=not allow_pending_publication,
    )
    typer.echo(render(result, OutputFormat(output)))
    if result["status"] != "passed":
        raise typer.Exit(code=1)


@reference_app.command("events")
def reference_events(
    sequence_from: int | None = typer.Option(None, "--sequence-from", min=0),
    sequence_to: int | None = typer.Option(None, "--sequence-to", min=0),
    limit: int = typer.Option(256, "--limit", min=1, max=4096),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    """Read the durable Reference lifecycle stream by stable sequence."""
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(
        render(
            _client(workspace).events(
                sequence_from=sequence_from,
                sequence_to=sequence_to,
                limit=limit,
            ),
            OutputFormat(output),
        )
    )


@reference_app.command("stream")
def reference_stream(
    aeron_dir: Path | None = typer.Option(None, "--aeron-dir"),
    timeout_seconds: int = typer.Option(30, "--timeout", min=1),
    idle_timeout_seconds: int = typer.Option(2, "--idle-timeout", min=1),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    """Observe pushed Aeron lifecycle batches until the stream becomes idle."""
    from kairospy.infrastructure.transport.reference import AeronReferenceEventSource
    from kairospy.surface.cli.options import OutputFormat, render

    result = asyncio.run(
        _observe_reference_stream(
            AeronReferenceEventSource(aeron_dir=aeron_dir),
            timeout_seconds=timeout_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
        )
    )
    typer.echo(render(result, OutputFormat(output)))


async def _observe_reference_stream(
    source: Any,
    *,
    timeout_seconds: int,
    idle_timeout_seconds: int,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    idle_deadline: float | None = None
    events = 0
    generation: int | None = None
    event_sequence: int | None = None
    first_event_id: str | None = None
    last_event_id: str | None = None
    stream = source.subscribe_live().__aiter__()
    try:
        while True:
            now = time.monotonic()
            next_deadline = min(
                deadline,
                idle_deadline if idle_deadline is not None else deadline,
            )
            remaining = next_deadline - now
            if remaining <= 0:
                if idle_deadline is not None and now >= idle_deadline:
                    break
                raise RuntimeError("no Reference event was received before timeout")
            try:
                event = await asyncio.wait_for(anext(stream), timeout=remaining)
            except TimeoutError:
                now = time.monotonic()
                if idle_deadline is not None and now >= idle_deadline:
                    break
                if events == 0 and now >= deadline:
                    raise RuntimeError(
                        "no Reference event was received before timeout"
                    ) from None
                continue
            except StopAsyncIteration:
                if events == 0:
                    raise RuntimeError(
                        "Reference event stream ended before receiving an event"
                    ) from None
                break
            events += 1
            generation = event.catalog_revision
            event_sequence = event.sequence
            if first_event_id is None:
                first_event_id = event.event_id
            last_event_id = event.event_id
            idle_deadline = time.monotonic() + idle_timeout_seconds
    finally:
        await source.close()
    return {
        "status": "received",
        "batches": events,
        "events": events,
        "generation": generation,
        "event_sequence": event_sequence,
        "first_event_id": first_event_id,
        "last_event_id": last_event_id,
    }


@reference_app.command("refresh")
def reference_refresh(
    source: str | None = typer.Option(None, "--source"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(render(_client(workspace).refresh(source=source), OutputFormat(output)))


@reference_app.command("pause")
def reference_pause(
    source: str = typer.Option(..., "--source"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    """Pause one provider without affecting the others."""
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(
        render(_client(workspace).set_source_paused(source, True), OutputFormat(output))
    )


@reference_app.command("resume")
def reference_resume(
    source: str = typer.Option(..., "--source"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    """Resume one paused provider."""
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(
        render(
            _client(workspace).set_source_paused(source, False), OutputFormat(output)
        )
    )


@reference_app.command("options-coverage")
def reference_options_coverage(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    """Show the explicit Massive stock-options coverage set."""
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(render(_client(workspace).option_coverage(), OutputFormat(output)))


@reference_app.command("options-add")
def reference_options_add(
    underlying: str = typer.Option(..., "--underlying"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    """Add one underlying to the dynamically managed Massive options coverage."""
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(
        render(
            _client(workspace).set_option_underlying(underlying, True),
            OutputFormat(output),
        )
    )


@reference_app.command("options-remove")
def reference_options_remove(
    underlying: str = typer.Option(..., "--underlying"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("json", "--output", "--format"),
) -> None:
    """Remove one underlying and reconcile it out of Massive coverage."""
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(
        render(
            _client(workspace).set_option_underlying(underlying, False),
            OutputFormat(output),
        )
    )


@reference_app.command("markets")
def reference_markets(
    symbol: str | None = typer.Option(None, "--symbol"),
    exchange_id: str | None = typer.Option(None, "--exchange-id", "--exchange"),
    market_type: str | None = typer.Option(None, "--market-type"),
    asset_type: str | None = typer.Option(None, "--asset-type"),
    active_only: bool = typer.Option(False, "--active-only"),
    status: str | None = typer.Option(None, "--status"),
    limit: int | None = typer.Option(None, "--limit"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("text", "--output", "--format"),
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render

    value = _client(workspace).markets(
        symbol=symbol,
        exchange_id=exchange_id,
        market_type=market_type,
        asset_type=asset_type,
        active_only=active_only,
        status=status,
        limit=limit,
    )
    typer.echo(render(value, OutputFormat(output)))


def _reference_collection_command(
    view: str, workspace: Path | None, output: str
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(render(_client(workspace).collection(view), OutputFormat(output)))


@reference_app.command("assets")
def reference_assets(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("table", "--output", "--format"),
) -> None:
    _reference_collection_command("assets", workspace, output)


@reference_app.command("entities")
def reference_entities(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("table", "--output", "--format"),
) -> None:
    _reference_collection_command("entities", workspace, output)


@reference_app.command("instruments")
def reference_instruments(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("table", "--output", "--format"),
) -> None:
    _reference_collection_command("instruments", workspace, output)


@reference_app.command("listings")
def reference_listings(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("table", "--output", "--format"),
) -> None:
    _reference_collection_command("listings", workspace, output)


@reference_app.command("execution-accesses")
def reference_execution_accesses(
    provider_id: str | None = typer.Option(None, "--provider-id", "--provider"),
    product_family: str | None = typer.Option(None, "--product-family"),
    provider_symbol: str | None = typer.Option(None, "--provider-symbol", "--symbol"),
    active_only: bool = typer.Option(False, "--active-only"),
    status: str | None = typer.Option(None, "--status"),
    limit: int | None = typer.Option(None, "--limit", min=1),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: str = typer.Option("table", "--output", "--format"),
) -> None:
    from kairospy.surface.cli.options import OutputFormat, render

    typer.echo(
        render(
            _client(workspace).execution_accesses(
                provider_id=provider_id,
                product_family=product_family,
                provider_symbol=provider_symbol,
                active_only=active_only,
                status=status,
                limit=limit,
            ),
            OutputFormat(output),
        )
    )


__all__ = ["reference_app"]
