"""Workspace-scoped Reference component commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import (
    _emit,
    _workspace_reference_client,
    system_component_reference_app,
)


@system_component_reference_app.command("status")
def system_component_reference_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the workspace-scoped Reference component server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status("reference"), output)


@system_component_reference_app.command("health")
def system_component_reference_health(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Reference runtime health through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_reference_client(owner).health().to_json_dict(), output)


@system_component_reference_app.command("providers")
def system_component_reference_providers(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Reference provider readiness through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_reference_client(owner).providers(), output)


@system_component_reference_app.command("catalog")
def system_component_reference_catalog(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read the Reference catalog from the workspace component."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_reference_client(owner).catalog().to_json_dict(), output)


@system_component_reference_app.command("validate")
def system_component_reference_validate(
    require_massive: bool = typer.Option(False, "--require-massive"),
    allow_pending_publication: bool = typer.Option(
        False, "--allow-pending-publication"
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Run Reference runtime acceptance checks against the selected component."""
    from kairospy.investment.apps.reference.application import (
        MASSIVE_REFERENCE_SOURCES,
        validate_reference_runtime,
    )

    owner = WorkspaceApplication().open(workspace)
    client = _workspace_reference_client(owner)
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
    _emit(result, output)
    if result["status"] != "passed":
        raise typer.Exit(code=1)


@system_component_reference_app.command("refresh")
def system_component_reference_refresh(
    source: str | None = typer.Option(None, "--source"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Request a Reference provider refresh on the workspace component."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_reference_client(owner).refresh(source=source), output)


@system_component_reference_app.command("pause")
def system_component_reference_pause(
    source: str = typer.Option(..., "--source"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Pause one Reference provider on the workspace component."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_reference_client(owner).set_source_paused(source, True), output)


@system_component_reference_app.command("resume")
def system_component_reference_resume(
    source: str = typer.Option(..., "--source"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Resume one Reference provider on the workspace component."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_reference_client(owner).set_source_paused(source, False), output)


@system_component_reference_app.command("options-coverage")
def system_component_reference_options_coverage(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Reference option coverage through the workspace component."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_reference_client(owner).option_coverage().to_json_dict(), output)


@system_component_reference_app.command("options-add")
def system_component_reference_options_add(
    underlying: str = typer.Option(..., "--underlying"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Add one underlying to Reference option coverage on the workspace component."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_reference_client(owner).set_option_underlying(underlying, True),
        output,
    )


@system_component_reference_app.command("options-remove")
def system_component_reference_options_remove(
    underlying: str = typer.Option(..., "--underlying"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Remove one underlying from Reference option coverage on the workspace component."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_reference_client(owner).set_option_underlying(underlying, False),
        output,
    )
