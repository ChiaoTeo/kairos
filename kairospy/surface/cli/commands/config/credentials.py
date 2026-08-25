"""Private workspace credential configuration commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.commands.config import (
    config_app,
    emit,
    open_resource_workbench,
)
from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.configuration.application import (
    ConfigurationReferenceApplication,
)
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


credential_config_app = typer.Typer(
    no_args_is_help=True, help="Configure private Workspace credentials"
)
config_app.add_typer(credential_config_app, name="credential")


@credential_config_app.command("list")
def credential_config_list(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    emit(CredentialConfigurationApplication(owner).list(), output)


@credential_config_app.command("setup")
def credential_config_setup(
    provider: str = typer.Option(..., "--provider"),
    credential_id: str | None = typer.Option(None, "--credential-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Open the single secure resource form instead of prompting in the CLI."""
    del provider, credential_id, output
    open_resource_workbench(workspace)


@credential_config_app.command("references")
def credential_config_references(
    credential_id: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Show every configuration location that currently uses a credential."""

    owner = WorkspaceApplication().open(workspace)
    emit(
        ConfigurationReferenceApplication(owner).credential_references(credential_id),
        output,
    )


@credential_config_app.command("delete")
def credential_config_delete(
    credential_id: str,
    force: bool = typer.Option(False, "--force"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Delete an unreferenced credential; --force leaves dependants unready."""

    owner = WorkspaceApplication().open(workspace)
    references = ConfigurationReferenceApplication(owner).credential_references(
        credential_id
    )
    if references and not force:
        locations = ", ".join(
            f"{item['source']}:{item['location']}" for item in references
        )
        raise typer.BadParameter(
            f"credential is referenced by configuration: {locations}; "
            "replace those references or use --force"
        )
    result = CredentialConfigurationApplication(owner).delete(credential_id)
    owner.paths.child(
        "state", "configuration", "models", f"{credential_id}.json"
    ).unlink(missing_ok=True)
    emit({**result, "references": references, "forced": force}, output)
