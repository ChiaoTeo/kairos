"""Workspace AI model connection commands."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.strategy.apps.agent.application import AgentResourceApplication
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


agent_config_app = typer.Typer(
    no_args_is_help=True,
    help="Configure and test Workspace AI model connections",
)
config_app.add_typer(agent_config_app, name="agent")


@agent_config_app.command("status")
def agent_config_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    emit(AgentResourceApplication(owner).status(), output)


@agent_config_app.command("setup")
def agent_config_setup(
    provider: str | None = typer.Option(None, "--provider"),
    connection_id: str | None = typer.Option(None, "--connection-id"),
    credential_id: str | None = typer.Option(None, "--credential-id"),
    api_mode: str | None = typer.Option(None, "--api-mode"),
    base_url: str | None = typer.Option(None, "--base-url"),
    model: str | None = typer.Option(None, "--model"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Open the single AI model resource form."""
    del provider, connection_id, credential_id, api_mode, base_url, model, output
    open_resource_workbench(workspace)


@agent_config_app.command("test")
def agent_config_test(
    connection_id: str,
    model: str = typer.Option(..., "--model"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Perform a user-triggered minimum model call and store secret-safe evidence."""

    owner = WorkspaceApplication().open(workspace)
    resources = AgentResourceApplication(owner)
    explicit = {
        str(item["connection_id"])
        for item in resources.model_connections()
        if item.get("api_mode") is not None
    }
    result = (
        resources.test_model_connection(connection_id, model)
        if connection_id in explicit
        else resources.test_openai_model(connection_id, model)
    )
    emit(result, output)


@agent_config_app.command("enable")
def agent_config_enable(
    connection_id: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    emit(
        AgentResourceApplication(owner).set_model_connection_enabled(
            connection_id, enabled=True
        ),
        output,
    )


@agent_config_app.command("disable")
def agent_config_disable(
    connection_id: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    emit(
        AgentResourceApplication(owner).set_model_connection_enabled(
            connection_id, enabled=False
        ),
        output,
    )


@agent_config_app.command("delete")
def agent_config_delete(
    connection_id: str,
    force: bool = typer.Option(False, "--force"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    resources = AgentResourceApplication(owner)
    connection = next(
        (
            value
            for value in resources.model_connections()
            if value.get("connection_id") == connection_id
        ),
        None,
    )
    if connection is None:
        raise typer.BadParameter(f"model connection does not exist: {connection_id}")
    references = ConfigurationReferenceApplication(owner).model_connection_references(
        connection_id
    )
    if references and not force:
        raise typer.BadParameter(
            "model connection is referenced by Launch configuration; "
            "replace those references or use --force"
        )
    emit(
        {
            **resources.delete_model_connection(connection_id),
            "references": references,
            "forced": force,
        },
        output,
    )
