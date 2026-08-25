"""Notification destination CLI commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from kairospy.strategy.apps.notification.application import NotificationAdminApplication
from kairospy.surface.cli.options import OutputFormat, render
from kairospy.system.apps.configuration.application import (
    ConfigurationReferenceApplication,
)
from kairospy.system.apps.launch.application import (
    LaunchNotificationConfigurationApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.surface.workbench import (
    WorkbenchLaunchRequest,
    WorkbenchWorkspaceError,
    run_workbench,
)


notifications_app = typer.Typer(no_args_is_help=True, help="Notification commands")


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


def _open_resource_workbench(workspace: Path | None) -> None:
    try:
        run_workbench(
            WorkbenchLaunchRequest(
                workspace=workspace,
                initial_section="resources",
                require_workspace=True,
            )
        )
    except WorkbenchWorkspaceError as error:
        raise typer.BadParameter(str(error)) from error


@notifications_app.command("list")
def notifications_list(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    destinations = [
        {
            **destination,
            "launch_references": ConfigurationReferenceApplication(
                owner
            ).destination_references(str(destination["destination_id"])),
        }
        for destination in NotificationAdminApplication(owner).list()
    ]
    _emit(destinations, output)


@notifications_app.command("setup")
def notifications_setup(
    provider: str | None = typer.Option(None, "--provider"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Open the single notification resource form."""
    del provider, output
    _open_resource_workbench(workspace)


@notifications_app.command("uses")
def notifications_uses(
    destination_id: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(
        LaunchNotificationConfigurationApplication(owner).references_to(destination_id),
        output,
    )


@notifications_app.command("disable")
def notifications_disable(
    destination_id: str,
    force: bool = typer.Option(False, "--force"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    references = ConfigurationReferenceApplication(owner).destination_references(
        destination_id
    )
    if references and not force:
        locations = ", ".join(
            f"{item['source']}:{item['location']}" for item in references
        )
        raise typer.BadParameter(
            f"destination is referenced by Launch routes: {locations}; "
            "replace those references or use --force"
        )
    _emit(
        {
            **NotificationAdminApplication(owner).set_enabled(destination_id, False),
            "references": references,
            "forced": force,
        },
        output,
    )


@notifications_app.command("delete")
def notifications_delete(
    destination_id: str,
    force: bool = typer.Option(False, "--force"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    references = ConfigurationReferenceApplication(owner).destination_references(
        destination_id
    )
    if references and not force:
        locations = ", ".join(
            f"{item['source']}:{item['location']}" for item in references
        )
        raise typer.BadParameter(
            f"destination is referenced by Launch routes: {locations}; "
            "detach it or use --force"
        )
    _emit(
        {
            **NotificationAdminApplication(owner).delete(destination_id),
            "references": references,
            "forced": force,
        },
        output,
    )


@notifications_app.command("attach")
def notifications_attach(
    destination_id: str,
    launch_id: str = typer.Option(..., "--launch"),
    route: str = typer.Option("signals", "--route"),
    default: bool = typer.Option(False, "--default-route"),
    lifecycle: bool = typer.Option(False, "--lifecycle-route"),
    required: bool = typer.Option(True, "--required/--optional"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    NotificationAdminApplication(owner).show(destination_id)
    _emit(
        LaunchNotificationConfigurationApplication(owner).attach(
            launch_id,
            destination_id,
            route=route,
            default=default,
            lifecycle=lifecycle,
            required=required,
        ),
        output,
    )


@notifications_app.command("detach")
def notifications_detach(
    destination_id: str,
    launch_id: str = typer.Option(..., "--launch"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(
        LaunchNotificationConfigurationApplication(owner).detach(
            launch_id, destination_id
        ),
        output,
    )


@notifications_app.command("validate")
def notifications_validate(
    mode: str = typer.Option("paper", "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(NotificationAdminApplication(owner).validate_workspace(mode=mode), output)


@notifications_app.command("test")
def notifications_test(
    destination_id: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    admin = NotificationAdminApplication(owner)
    try:
        result = asyncio.run(
            NotificationAdminApplication(owner).test_destination(destination_id)
        )
    except Exception as error:
        admin.record_test(
            destination_id,
            succeeded=False,
            detail=f"{type(error).__name__}: delivery test failed",
        )
        raise typer.BadParameter(
            f"{type(error).__name__}: delivery test failed"
        ) from None
    admin.record_test(destination_id, succeeded=True)
    _emit(result, output)


__all__ = ["notifications_app"]
