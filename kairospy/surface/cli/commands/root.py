from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
import time
from typing import Any

import typer

from kairospy.strategy.apps.agent.application import AgentResourceApplication
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.system.apps.launch.application import (
    LaunchControlApplication,
    LaunchNotificationConfigurationApplication,
    LaunchRegistryApplication,
    WorkspaceComponentDependencyApplication,
)
from kairospy.system.apps.components.application import (
    CapitalSystemClient,
    ComponentControlApplication,
    ComponentProcessApplication,
    NativeCliApplication,
    RiskSystemClient,
    SystemRuntimeSupervisor,
)
from kairospy.system.apps.components.application.process_logging import (
    current_run_id,
    decode_log_event,
    filter_log_lines,
    parse_since,
)
from kairospy.system.apps.configuration.application import (
    ConfigApplication,
    ConfigurationMigrationApplication,
    ConfigurationReferenceApplication,
)
from kairospy.strategy.apps.notification.application import NotificationAdminApplication
from kairospy.investment.apps.reference.application import ReferenceProviderConfigurationApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.surface.cli.options import OutputFormat, effective_output, render


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


def _open_resource_workbench(workspace: Path | None) -> None:
    from kairospy.surface.workbench import KairosWorkbenchApp, load_workbench_state

    state = load_workbench_state(workspace)
    if state.owner is None:
        raise typer.BadParameter(state.load_error or "当前没有可用的 workspace")
    KairosWorkbenchApp(state, initial_section="resources").run()


def _is_active_launch_status(status: Mapping[str, Any]) -> bool:
    return status.get("status") not in {
        "not_running",
        "stopped",
        "failed",
        "completed",
    }


def _component_dependents(owner: Any, component: str) -> list[dict[str, Any]]:
    return list(WorkspaceComponentDependencyApplication(owner).active(component))


def _ensure_no_active_component_dependents(
    owner: Any, component: str, action: str
) -> None:
    try:
        WorkspaceComponentDependencyApplication(owner).require_clear(component, action)
    except RuntimeError as error:
        raise typer.BadParameter(str(error)) from error


def _run_workspace_market_connected_command(
    owner: Any, command: str, arguments: list[str]
) -> dict[str, Any]:
    socket = owner.paths.process_socket("market")
    if not socket.exists():
        raise typer.BadParameter(
            "无法连接 workspace Market 服务。"
            "请先运行：kairos system component market status"
        )
    value = NativeCliApplication(owner).run(
        "market",
        [
            "connected",
            command,
            "--socket",
            str(socket),
            "--view-root",
            str(owner.paths.child("snapshots", "market", "market-shared")),
            *arguments,
        ],
    )
    return {**value, "scope": "system"}


def _workspace_reference_client(owner: Any):
    socket = owner.paths.process_socket("reference")
    if not socket.exists():
        raise typer.BadParameter(
            "target server not found: workspace Reference is not running; "
            "use `kairos system up --component reference` first"
        )
    from kairospy.investment.apps.reference.application import ReferenceApplication

    return ReferenceApplication.from_process(
        socket_path=socket,
        database_path=owner.paths.reference_database(),
        timeout=30.0,
    )


def _workspace_risk_client(owner: Any) -> RiskSystemClient:
    socket = owner.paths.process_socket("risk")
    if not socket.exists():
        raise typer.BadParameter(
            "target server not found: workspace Risk is not running; "
            "use `kairos system up --component risk` first"
        )
    return RiskSystemClient(
        socket,
        view_root=owner.paths.snapshots,
        timeout=30.0,
    )


def _run_workspace_risk_connected_command(
    owner: Any, command: str, arguments: list[str]
) -> dict[str, Any]:
    return NativeCliApplication(owner).run("risk", ["connected", command, *arguments])


def _workspace_capital_client(owner: Any) -> CapitalSystemClient:
    socket = owner.paths.process_socket("capital")
    if not socket.exists():
        raise typer.BadParameter(
            "target server not found: workspace Capital is not running; "
            "use `kairos system up --component capital` first"
        )
    return CapitalSystemClient(
        socket,
        view_root=owner.paths.snapshots,
        timeout=30.0,
    )


def _run_workspace_capital_connected_command(
    owner: Any, command: str, arguments: list[str]
) -> dict[str, Any]:
    return NativeCliApplication(owner).run(
        "capital", ["connected", command, *arguments]
    )


def _add_group(
    parent: typer.Typer, name: str, commands: tuple[str, ...]
) -> typer.Typer:
    group = typer.Typer(no_args_is_help=True, help=f"{name} commands")
    parent.add_typer(group, name=name)
    del commands
    return group


project_app = typer.Typer(no_args_is_help=True, help="Project commands")
config_app = typer.Typer(no_args_is_help=True, help="Configuration commands")
system_app = typer.Typer(no_args_is_help=True, help="System runtime commands")
system_component_app = typer.Typer(
    no_args_is_help=True, help="Connect to workspace-scoped runtime components"
)
system_component_market_app = typer.Typer(
    no_args_is_help=True, help="Connect to the workspace-scoped Market component"
)
system_component_reference_app = typer.Typer(
    no_args_is_help=True, help="Connect to the workspace-scoped Reference component"
)
system_component_risk_app = typer.Typer(
    no_args_is_help=True, help="Connect to the workspace-scoped Risk component"
)
system_component_capital_app = typer.Typer(
    no_args_is_help=True, help="Connect to the workspace-scoped Capital component"
)
system_app.add_typer(system_component_app, name="component")
system_component_app.add_typer(system_component_market_app, name="market")
system_component_app.add_typer(system_component_reference_app, name="reference")
system_component_app.add_typer(system_component_risk_app, name="risk")
system_component_app.add_typer(system_component_capital_app, name="capital")
notifications_app = typer.Typer(no_args_is_help=True, help="Notification commands")


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
    import asyncio

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


@project_app.command(
    "init", help="Create a Kairos project, optionally with a runnable starter."
)
def project_init(
    root: Path | None = typer.Argument(
        None, help="Project directory (prompted when omitted)"
    ),
    workspace_id: str | None = typer.Option(None, "--id"),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Do not prompt; require the project directory and --id",
    ),
    template: str | None = typer.Option(
        None,
        "--template",
        help="Install a runnable starter; currently supported: backtest.",
    ),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    template_name = template.strip().lower() if template is not None else None
    if root is None:
        raise typer.BadParameter(
            "project directory is required; use `kairos interactive` for guided setup"
        )
    else:
        root = Path(root)

    default_id = root.expanduser().resolve().name
    if workspace_id is None:
        raise typer.BadParameter(
            f"--id is required (suggested: {default_id}); "
            "use `kairos interactive` for guided setup"
        )

    try:
        workspace = WorkspaceApplication().init_project(
            root, workspace_id=workspace_id, template=template_name
        )
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--template") from error
    next_steps = (
        [
            "cd " + str(workspace.paths.project_root),
            "kairos launch start demo-backtest",
            "kairos launch wait demo-backtest",
        ]
        if template_name == "backtest"
        else ["add a launch config under .kairos/config/launches"]
    )
    _emit(
        {
            "status": "initialized",
            "workspace_id": workspace.workspace_id,
            "root": str(workspace.paths.root),
            "template": template_name,
            "next_steps": next_steps,
        },
        output,
    )


@project_app.command("status", help="Show the resolved project and workspace paths.")
def project_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    value = WorkspaceApplication().open(workspace)
    _emit({"workspace_id": value.workspace_id, "root": str(value.paths.root)}, output)


@project_app.command("scaffold")
def project_scaffold(
    template: str = typer.Option(
        "backtest", "--template", help="Starter to install; currently: backtest."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Install a runnable starter into an existing project."""

    owner = WorkspaceApplication().open(workspace)
    try:
        created = WorkspaceApplication().install_template(owner, template=template)
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error), param_hint="--template") from error
    _emit(
        {
            "status": "scaffolded",
            "template": template.strip().lower(),
            "created": [str(path) for path in created],
            "next_steps": [
                "kairos launch start demo-backtest",
                "kairos launch wait demo-backtest",
            ],
        },
        output,
    )


@project_app.command("doctor", help="Check project readiness and show the next action.")
def project_doctor(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).doctor(), output)


@system_app.command("inspect")
def system_inspect(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status(component), output)


@system_app.command("attach")
def system_attach(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(
        {"component": component, "socket": str(owner.paths.process_socket(component))},
        output,
    )


@system_app.command("command")
def system_command(
    component: str = typer.Option(..., "--component"),
    command: str = typer.Option(..., "--command"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    control = ComponentProcessApplication(owner).ensure_running("control")
    _emit(control.command(component, {"type": command}), output)


@system_app.command("up")
def system_up(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system up manages only workspace services: reference and market; "
            "launch starts instance-owned components"
        )
    owner = WorkspaceApplication().open(workspace)
    process = ComponentProcessApplication(owner)
    control = process.ensure_running(
        component,
        account_id=account_id,
        stream_startup_logs=component == "reference"
        and effective_output(output) is OutputFormat.TEXT,
    )
    supervisor = SystemRuntimeSupervisor(process)
    supervisor.register(component, {"account_id": account_id} if account_id else {})
    supervisor.start_background()
    _emit(control.status(), output)


@system_app.command("down")
def system_down(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system down manages only workspace services: reference and market; "
            "use launch stop for instance-owned components"
        )
    owner = WorkspaceApplication().open(workspace)
    _ensure_no_active_component_dependents(owner, component, "down")
    process = ComponentProcessApplication(owner)
    supervisor = SystemRuntimeSupervisor(process)
    supervisor.unregister(component)
    _emit(process.stop(component), output)


@system_app.command("restart")
def system_restart(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system restart manages only workspace services: reference and market; "
            "use launch start/stop for instance-owned components"
        )
    owner = WorkspaceApplication().open(workspace)
    _ensure_no_active_component_dependents(owner, component, "restart")
    process = ComponentProcessApplication(owner)
    text_output = effective_output(output) is OutputFormat.TEXT
    control = process.restart(
        component,
        account_id=account_id,
        stream_startup_logs=component == "reference" and text_output,
        progress=typer.echo if text_output else None,
    )
    supervisor = SystemRuntimeSupervisor(process)
    supervisor.register(component, {"account_id": account_id} if account_id else {})
    supervisor.start_background()
    _emit(control.status(), output)


@config_app.command("paths")
def config_paths(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).paths(), output)


@config_app.command("manifest")
def config_manifest(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).manifest(), output)


@config_app.command("show")
def config_show(
    workspace: Path = typer.Option(None, "--workspace"),
    name: str | None = typer.Option(None, "--name"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).show(name), output)


@config_app.command("doctor")
def config_doctor(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).doctor(), output)


@config_app.command("migrate")
def config_migrate_preview(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Preview legacy upgrades; this command never modifies configuration."""

    owner = WorkspaceApplication().open(workspace)
    _emit(ConfigurationMigrationApplication(owner).preview(), output)


@config_app.command("explain")
def config_explain(
    name: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(
        ConfigApplication(WorkspaceApplication().open(workspace)).explain(name), output
    )


@config_app.command("operations")
def config_operations(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(
        ConfigApplication(WorkspaceApplication().open(workspace)).operations(), output
    )


profile_app = typer.Typer(no_args_is_help=True, help="Configuration profiles")
config_app.add_typer(profile_app, name="profile")

credential_config_app = typer.Typer(
    no_args_is_help=True, help="Configure Workspace external-service SecretRefs"
)
config_app.add_typer(credential_config_app, name="credential")

data_config_app = typer.Typer(
    no_args_is_help=True,
    help="Configure and manually test shared Workspace data providers",
)
config_app.add_typer(data_config_app, name="data")


@credential_config_app.command("list")
def credential_config_list(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(CredentialConfigurationApplication(owner).list(), output)


@credential_config_app.command("setup")
def credential_config_setup(
    provider: str = typer.Option(..., "--provider"),
    credential_id: str | None = typer.Option(None, "--credential-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Open the single secure resource form instead of prompting in the CLI."""
    del provider, credential_id, output
    _open_resource_workbench(workspace)

@credential_config_app.command("references")
def credential_config_references(
    credential_id: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Show every configuration location that currently uses a credential."""

    owner = WorkspaceApplication().open(workspace)
    _emit(
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
    _emit({**result, "references": references, "forced": force}, output)


@data_config_app.command("list")
def data_config_list(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(ReferenceProviderConfigurationApplication(owner).list(), output)


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
    _open_resource_workbench(workspace)

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
    _emit(
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
    _emit(
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
    _emit(
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
    _emit(
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
    _emit({**result, "references": references, "forced": force}, output)


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
    _emit(AgentResourceApplication(owner).status(), output)


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
    _open_resource_workbench(workspace)

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
    _emit(result, output)


@agent_config_app.command("enable")
def agent_config_enable(
    connection_id: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(
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
    _emit(
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
    _emit(
        {
            **resources.delete_model_connection(connection_id),
            "references": references,
            "forced": force,
        },
        output,
    )


@profile_app.command("list")
def profile_list(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).profiles(), output)


@profile_app.command("create")
def profile_create(
    name: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(
        {
            "path": str(
                ConfigApplication(
                    WorkspaceApplication().open(workspace)
                ).create_profile(name)
            )
        },
        output,
    )


@profile_app.command("use")
def profile_use(
    name: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(
        {
            "path": str(
                ConfigApplication(WorkspaceApplication().open(workspace)).use_profile(
                    name
                )
            ),
            "profile": name,
        },
        output,
    )


@config_app.command("status")
def config_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(
        {"workspace_id": owner.workspace_id, "config": str(owner.paths.config)}, output
    )


@system_app.command("status")
def system_status(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status(component), output)


@system_component_app.command("status")
def system_component_status(
    component: str = typer.Argument(..., help="Workspace-scoped component name."),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect one workspace-scoped component server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status(component), output)


@system_component_market_app.command("status")
def system_component_market_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the workspace-scoped Market component server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        {**ComponentProcessApplication(owner).status("market"), "scope": "system"},
        output,
    )


@system_component_market_app.command("routes")
def system_component_market_routes(
    workspace: Path = typer.Option(None, "--workspace"),
    market_id: str | None = typer.Option(None, "--market-id"),
    instrument_id: str | None = typer.Option(None, "--instrument-id"),
    observation_kind: str | None = typer.Option(None, "--observation-kind"),
    provider: str | None = typer.Option(None, "--provider"),
    configured_only: bool = typer.Option(False, "--configured-only"),
    ready_only: bool = typer.Option(False, "--ready-only"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read provider-route readiness from the workspace-scoped Market server."""
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
    _emit(_run_workspace_market_connected_command(owner, "routes", arguments), output)


@system_component_market_app.command("snapshot")
def system_component_market_snapshot(
    kind: str = typer.Argument(..., help="Snapshot kind: quote, bar, or greeks."),
    market_id: str | None = typer.Option(None, "--market-id"),
    provider: str | None = typer.Option(None, "--provider"),
    symbol: str | None = typer.Option(None, "--symbol"),
    exchange: str = typer.Option("binance", "--exchange"),
    market_type: str = typer.Option("spot", "--market-type"),
    timeframe: str | None = typer.Option(None, "--timeframe"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read one current Market current view from the workspace scope."""
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
    _emit(_run_workspace_market_connected_command(owner, "snapshot", arguments), output)


@system_component_market_app.command("freshness")
def system_component_market_freshness(
    market_id: str = typer.Option(..., "--market-id"),
    observation: str | None = typer.Option(None, "--observation"),
    provider: str | None = typer.Option(None, "--provider"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read one current Market freshness current view from the workspace scope."""
    owner = WorkspaceApplication().open(workspace)
    arguments = ["--market-id", market_id]
    if observation is not None:
        arguments.extend(["--observation", observation])
    if provider is not None:
        arguments.extend(["--provider", provider])
    _emit(
        _run_workspace_market_connected_command(owner, "freshness", arguments), output
    )


@system_component_market_app.command("subscribe")
def system_component_market_subscribe(
    subscription_id: str = typer.Option(..., "--subscription-id"),
    market_id: str = typer.Option(..., "--market-id"),
    strategy_id: str = typer.Option("cli", "--strategy-id"),
    instance_id: str = typer.Option("cli", "--instance-id"),
    data: list[str] = typer.Option(..., "--data"),
    prefer_provider: list[str] = typer.Option([], "--prefer-provider"),
    require_provider: list[str] = typer.Option([], "--require-provider"),
    all_eligible_providers: bool = typer.Option(False, "--all-eligible-providers"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Create a runtime subscription on the workspace-scoped Market server."""
    policies = bool(prefer_provider) + bool(require_provider) + all_eligible_providers
    if policies > 1:
        raise typer.BadParameter("select only one provider policy")
    arguments = [
        "--subscription-id",
        subscription_id,
        "--market-id",
        market_id,
        "--strategy-id",
        strategy_id,
        "--instance-id",
        instance_id,
    ]
    for value in data:
        arguments.extend(["--data", value])
    for value in prefer_provider:
        arguments.extend(["--prefer-provider", value])
    for value in require_provider:
        arguments.extend(["--require-provider", value])
    if all_eligible_providers:
        arguments.append("--all-eligible-providers")
    owner = WorkspaceApplication().open(workspace)
    value = _run_workspace_market_connected_command(owner, "subscribe", arguments)
    _emit(value, output)


@system_component_market_app.command("unsubscribe")
def system_component_market_unsubscribe(
    subscription_id: str = typer.Option(..., "--subscription-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Remove a runtime subscription from the workspace-scoped Market server."""
    owner = WorkspaceApplication().open(workspace)
    value = _run_workspace_market_connected_command(
        owner, "unsubscribe", ["--subscription-id", subscription_id]
    )
    _emit(value, output)


@system_component_market_app.command("recover")
def system_component_market_recover(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Request bounded source recovery on the workspace-scoped Market server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_run_workspace_market_connected_command(owner, "recover", []), output)


@system_component_market_app.command("pause-replay")
def system_component_market_pause_replay(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Pause Market replay input on the workspace-scoped Market server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_run_workspace_market_connected_command(owner, "pause-replay", []), output)


@system_component_market_app.command("resume-replay")
def system_component_market_resume_replay(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Resume Market replay input on the workspace-scoped Market server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_run_workspace_market_connected_command(owner, "resume-replay", []), output)


@system_component_market_app.command("dependents")
def system_component_market_dependents(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """List running launch instances that use the workspace-scoped Market."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        {
            "component": "market",
            "scope": "workspace",
            "dependents": _component_dependents(owner, "market"),
        },
        output,
    )


@system_component_reference_app.command("status")
def system_component_reference_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the workspace-scoped Reference component server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status("reference"), output)


@system_component_risk_app.command("status")
def system_component_risk_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the workspace-scoped Risk component server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status("risk"), output)


@system_component_risk_app.command("health")
def system_component_risk_health(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Risk runtime health through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_risk_client(owner).health(), output)


@system_component_risk_app.command("latest")
def system_component_risk_latest(
    actor_id: str = typer.Option("risk", "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Risk latest-view business facts from the workspace component."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_risk_client(owner).latest_metadata(actor_id=actor_id), output)


@system_component_risk_app.command("limits")
def system_component_risk_limits(
    actor_id: str = typer.Option("risk", "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Risk limit usage resources from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_risk_client(owner).latest_limits(actor_id=actor_id), output)


@system_component_risk_app.command("reservations")
def system_component_risk_reservations(
    actor_id: str = typer.Option("risk", "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Risk active reservations from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_risk_client(owner).latest_reservations(actor_id=actor_id), output)


@system_component_risk_app.command("circuits")
def system_component_risk_circuits(
    actor_id: str = typer.Option("risk", "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Risk circuit states from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_risk_client(owner).latest_circuits(actor_id=actor_id), output)


@system_component_risk_app.command("pre-trade-check")
def system_component_risk_pre_trade_check(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Evaluate a Risk runtime authorization request through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner, "pre-trade-check", ["--file", str(file)]
        ),
        output,
    )


@system_component_risk_app.command("authorize-reserve")
def system_component_risk_authorize_reserve(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Authorize and reserve Risk runtime budget through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner, "authorize-reserve", ["--file", str(file)]
        ),
        output,
    )


@system_component_risk_app.command("release")
def system_component_risk_release(
    reservation_id: str = typer.Option(..., "--reservation-id"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Release a Risk runtime reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner,
            "release",
            [
                "--reservation-id",
                reservation_id,
                "--at-unix-nanos",
                str(at_unix_nanos),
            ],
        ),
        output,
    )


@system_component_risk_app.command("consume")
def system_component_risk_consume(
    reservation_id: str = typer.Option(..., "--reservation-id"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Consume a Risk runtime reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner,
            "consume",
            [
                "--reservation-id",
                reservation_id,
                "--at-unix-nanos",
                str(at_unix_nanos),
            ],
        ),
        output,
    )


@system_component_risk_app.command("resize")
def system_component_risk_resize(
    reservation_id: str = typer.Option(..., "--reservation-id"),
    amount: str = typer.Option(..., "--amount"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Resize a Risk runtime reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner,
            "resize",
            [
                "--reservation-id",
                reservation_id,
                "--amount",
                amount,
                "--at-unix-nanos",
                str(at_unix_nanos),
            ],
        ),
        output,
    )


def _risk_circuit_arguments(
    *,
    account_id: str | None,
    strategy_id: str | None,
    exchange_id: str | None,
) -> list[str]:
    arguments: list[str] = []
    if account_id is not None:
        arguments.extend(["--account-id", account_id])
    if strategy_id is not None:
        arguments.extend(["--strategy-id", strategy_id])
    if exchange_id is not None:
        arguments.extend(["--exchange-id", exchange_id])
    return arguments


@system_component_risk_app.command("open-circuit")
def system_component_risk_open_circuit(
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    reason: str = typer.Option(..., "--reason"),
    reset_at_unix_nanos: int | None = typer.Option(None, "--reset-at-unix-nanos"),
    account_id: str | None = typer.Option(None, "--account-id"),
    strategy_id: str | None = typer.Option(None, "--strategy-id"),
    exchange_id: str | None = typer.Option(None, "--exchange-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Open a Risk runtime circuit through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    arguments = [
        "--at-unix-nanos",
        str(at_unix_nanos),
        "--reason",
        reason,
        *_risk_circuit_arguments(
            account_id=account_id,
            strategy_id=strategy_id,
            exchange_id=exchange_id,
        ),
    ]
    if reset_at_unix_nanos is not None:
        arguments.extend(["--reset-at-unix-nanos", str(reset_at_unix_nanos)])
    _emit(
        _run_workspace_risk_connected_command(owner, "open-circuit", arguments),
        output,
    )


@system_component_risk_app.command("close-circuit")
def system_component_risk_close_circuit(
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    account_id: str | None = typer.Option(None, "--account-id"),
    strategy_id: str | None = typer.Option(None, "--strategy-id"),
    exchange_id: str | None = typer.Option(None, "--exchange-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Close a Risk runtime circuit through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner,
            "close-circuit",
            [
                "--at-unix-nanos",
                str(at_unix_nanos),
                *_risk_circuit_arguments(
                    account_id=account_id,
                    strategy_id=strategy_id,
                    exchange_id=exchange_id,
                ),
            ],
        ),
        output,
    )


@system_component_risk_app.command("publish-policy")
def system_component_risk_publish_policy(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Publish a Risk runtime policy through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner, "publish-policy", ["--file", str(file)]
        ),
        output,
    )


@system_component_risk_app.command("advance-time")
def system_component_risk_advance_time(
    event_time_unix_nanos: int = typer.Option(..., "--event-time-unix-nanos"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Advance Risk runtime time through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_risk_connected_command(
            owner,
            "advance-time",
            ["--event-time-unix-nanos", str(event_time_unix_nanos)],
        ),
        output,
    )


@system_component_capital_app.command("status")
def system_component_capital_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the workspace-scoped Capital component server."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status("capital"), output)


@system_component_capital_app.command("health")
def system_component_capital_health(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Capital runtime health through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_capital_client(owner).health(), output)


@system_component_capital_app.command("current")
def system_component_capital_current(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital current-view business facts from the workspace component."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_metadata(capital_group_id),
        output,
    )


@system_component_capital_app.command("availabilities")
def system_component_capital_availabilities(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital availability facts from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_availabilities(capital_group_id),
        output,
    )


@system_component_capital_app.command("objectives")
def system_component_capital_objectives(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital funding objectives from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_objectives(capital_group_id),
        output,
    )


@system_component_capital_app.command("demands")
def system_component_capital_demands(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital demands from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_demands(capital_group_id),
        output,
    )


@system_component_capital_app.command("plans")
def system_component_capital_plans(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital plans from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_plans(capital_group_id),
        output,
    )


@system_component_capital_app.command("routes")
def system_component_capital_routes(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital routes from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_routes(capital_group_id),
        output,
    )


@system_component_capital_app.command("reservations")
def system_component_capital_reservations(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital reservations from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_reservations(capital_group_id),
        output,
    )


@system_component_capital_app.command("operations")
def system_component_capital_operations(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital operations from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _workspace_capital_client(owner).current_operations(capital_group_id),
        output,
    )


@system_component_capital_app.command("alerts")
def system_component_capital_alerts(
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Capital recovery alerts from the workspace component mmap."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_capital_client(owner).current_alerts(capital_group_id), output)


@system_component_capital_app.command("publish-funding-objective")
def system_component_capital_publish_funding_objective(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Publish a Capital runtime funding objective through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_capital_connected_command(
            owner, "publish-funding-objective", ["--file", str(file)]
        ),
        output,
    )


@system_component_capital_app.command("observe-demand")
def system_component_capital_observe_demand(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Observe a Capital runtime demand through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_capital_connected_command(
            owner, "observe-demand", ["--file", str(file)]
        ),
        output,
    )


@system_component_capital_app.command("cancel-funding-objective")
def system_component_capital_cancel_funding_objective(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Cancel a Capital runtime funding objective through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_capital_connected_command(
            owner, "cancel-funding-objective", ["--file", str(file)]
        ),
        output,
    )


@system_component_capital_app.command("reconcile-plan")
def system_component_capital_reconcile_plan(
    file: Path = typer.Option(..., "--file"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Reconcile a Capital runtime plan through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_workspace_capital_connected_command(
            owner, "reconcile-plan", ["--file", str(file)]
        ),
        output,
    )


@system_component_reference_app.command("health")
def system_component_reference_health(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Reference runtime health through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(_workspace_reference_client(owner).health(), output)


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
    _emit(_workspace_reference_client(owner).catalog(), output)


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
    _emit(_workspace_reference_client(owner).option_coverage(), output)


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


@system_app.command("list")
def system_list(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """List workspace-scoped system components without starting them."""
    output = effective_output(output)
    owner = WorkspaceApplication().open(workspace)
    value = ComponentProcessApplication(owner).list_status()
    if output is OutputFormat.JSON:
        _emit(value, output)
        return
    _emit(
        [
            {
                "component": component,
                "status": status.get("status", "unknown"),
                "pid": status.get("pid", ""),
                "pid_alive": status.get("pid_alive", ""),
                "control_socket": status.get("control_socket", ""),
                "log_file": status.get("log_file", ""),
            }
            for component, status in value.items()
        ],
        OutputFormat.TABLE,
    )


@system_app.command("logs")
def system_logs(
    component_argument: str | None = typer.Argument(
        None,
        metavar="COMPONENT",
        help="Component name (legacy positional form).",
    ),
    component: str | None = typer.Option(
        None,
        "--component",
        help="Component name, for example account or execution.",
    ),
    lines: int = typer.Option(
        100, "--lines", min=0, help="Number of recent lines to show."
    ),
    follow: bool = typer.Option(
        False, "-f", "--follow", help="Continue printing new output."
    ),
    current_run: bool = typer.Option(
        False, "--current-run", help="Show only the most recent process run."
    ),
    since: str | None = typer.Option(
        None, "--since", help="Show events since a duration (10m) or RFC3339 timestamp."
    ),
    level: str | None = typer.Option(None, "--level", help="Filter by log level."),
    event_name: str | None = typer.Option(
        None, "--event", help="Filter by structured event name."
    ),
    provider: str | None = typer.Option(
        None, "--provider", help="Filter by provider field."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Show a component's combined stdout/stderr log."""
    if (
        component is not None
        and component_argument is not None
        and component != component_argument
    ):
        raise typer.BadParameter(
            "component was specified twice with different values; "
            "use --component COMPONENT"
        )
    component = component or component_argument
    if component is None:
        raise typer.BadParameter("component is required; use --component COMPONENT")
    components = {
        "reference",
        "market",
        "account",
        "risk",
        "execution",
        "aeron",
        "system-supervisor",
    }
    if component not in components:
        raise typer.BadParameter(f"unsupported component: {component}")
    if follow and effective_output(output) is not OutputFormat.TEXT:
        raise typer.BadParameter("--follow currently supports text output only")
    owner = WorkspaceApplication().open(workspace)
    path = owner.paths.logs / component / "process.log"
    try:
        since_time = parse_since(since)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--since") from error
    content = (
        path.read_text(encoding="utf-8", errors="replace").splitlines()
        if path.is_file()
        else []
    )
    selected_run_id = current_run_id(content) if current_run else None
    filtered = filter_log_lines(
        content,
        level=level,
        event_name=event_name,
        provider=provider,
        since=since_time,
        run_id=selected_run_id,
    )
    visible = filtered[-lines:] if lines else []
    if effective_output(output) is OutputFormat.JSON:
        value = {
            "component": component,
            "path": str(path),
            "exists": path.is_file(),
            "run_id": selected_run_id,
            "lines": visible,
        }
        _emit(value, output)
        return
    if path.is_file() and visible:
        typer.echo("\n".join(visible))
    elif not path.is_file():
        typer.echo(f"log file does not exist: {path}")
    if not follow:
        return
    position = path.stat().st_size if path.is_file() else 0
    inode = path.stat().st_ino if path.is_file() else None
    try:
        while True:
            if path.is_file():
                current_stat = path.stat()
                if inode != current_stat.st_ino or current_stat.st_size < position:
                    position = 0
                    inode = current_stat.st_ino
                    selected_run_id = None
                with path.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(position)
                    for line in stream:
                        rendered = line.rstrip("\n")
                        value = decode_log_event(rendered)
                        if (
                            current_run
                            and value is not None
                            and value.get("event") == "process_spawned"
                        ):
                            selected_run_id = value.get("run_id")
                        if filter_log_lines(
                            [rendered],
                            level=level,
                            event_name=event_name,
                            provider=provider,
                            since=since_time,
                            run_id=selected_run_id if current_run else None,
                        ):
                            typer.echo(rendered, color=False)
                    position = stream.tell()
            time.sleep(0.25)
    except KeyboardInterrupt:
        return


@system_app.command("doctor")
def system_doctor(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Diagnose sockets, health files, locks, and unresponsive components."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).doctor(), output)


@system_app.command("repair")
def system_repair(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Remove only confirmed stale runtime resources."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).repair(), output)


@system_app.command("supervise")
def system_supervise(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    interval: float = typer.Option(1.0, "--interval", min=0.1),
    once: bool = typer.Option(False, "--once"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Run the workspace runtime reconciler for one desired component."""
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system supervise manages only workspace services: reference and market; "
            "launch owns instance components"
        )
    owner = WorkspaceApplication().open(workspace)
    desired: dict[str, object] = {}
    if component == "account" and account_id:
        desired["account_id"] = account_id
    supervisor = SystemRuntimeSupervisor(
        ComponentProcessApplication(owner),
        desired={component: desired},
    )
    value = supervisor.reconcile_once()
    if once:
        _emit(value[component], output)
        return
    supervisor.run_forever(interval=interval)
