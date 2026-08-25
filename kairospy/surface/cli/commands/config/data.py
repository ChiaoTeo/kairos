"""Workspace reference-data provider configuration commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.investment.apps.reference.application import (
    ReferenceProviderConfigurationApplication,
)
from kairospy.surface.cli.commands.config import (
    config_app,
    emit,
    open_resource_workbench,
)
from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.configuration.application import (
    ConfigurationReferenceApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


data_config_app = typer.Typer(
    no_args_is_help=True,
    help="Configure and manually test shared Workspace data providers",
)
config_app.add_typer(data_config_app, name="data")


@data_config_app.command("list")
def data_config_list(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    emit(ReferenceProviderConfigurationApplication(owner).list(), output)


@data_config_app.command("setup")
def data_config_setup(
    credential_id: str | None = typer.Option(None, "--credential-id"),
    endpoint: str = typer.Option("https://api.massive.com", "--endpoint"),
    options: bool = typer.Option(False, "--options"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Open the single Market data resource form."""
    del credential_id, endpoint, options, output
    open_resource_workbench(workspace)


@data_config_app.command("test")
def data_config_test(
    connection_id: str = typer.Argument("massive"),
    confirmed: bool = typer.Option(False, "--yes"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read fixed samples after explicit script confirmation."""
    if not confirmed:
        raise typer.BadParameter(
            "manual data test requires explicit --yes; use `kairos interactive` "
            "for guided confirmation"
        )
    owner = WorkspaceApplication().open(workspace)
    emit(
        ReferenceProviderConfigurationApplication(owner).test_connection(connection_id),
        output,
    )


@data_config_app.command("disable")
def data_config_disable(
    connection_id: str = typer.Argument("massive"),
    force: bool = typer.Option(False, "--force"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    references = ConfigurationReferenceApplication(owner).data_provider_references(
        connection_id
    )
    if references and not force:
        locations = ", ".join(
            f"{item['source']}:{item['location']}" for item in references
        )
        raise typer.BadParameter(
            f"data connection is referenced by Launch configuration: {locations}; "
            "replace those references or use --force"
        )
    emit(
        {
            **ReferenceProviderConfigurationApplication(owner).set_enabled(
                connection_id, enabled=False
            ),
            "references": references,
            "forced": force,
        },
        output,
    )


@data_config_app.command("enable")
def data_config_enable(
    connection_id: str = typer.Argument("massive"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    emit(
        ReferenceProviderConfigurationApplication(owner).set_enabled(
            connection_id, enabled=True
        ),
        output,
    )


@data_config_app.command("references")
def data_config_references(
    connection_id: str = typer.Argument("massive"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    emit(
        ConfigurationReferenceApplication(owner).data_provider_references(
            connection_id
        ),
        output,
    )


@data_config_app.command("delete")
def data_config_delete(
    connection_id: str = typer.Argument("massive"),
    force: bool = typer.Option(False, "--force"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    references = ConfigurationReferenceApplication(owner).data_provider_references(
        connection_id
    )
    if references and not force:
        locations = ", ".join(
            f"{item['source']}:{item['location']}" for item in references
        )
        raise typer.BadParameter(
            f"data connection is referenced by Launch configuration: {locations}; "
            "replace those references or use --force"
        )
    result = ReferenceProviderConfigurationApplication(owner).delete(connection_id)
    emit({**result, "references": references, "forced": force}, output)
