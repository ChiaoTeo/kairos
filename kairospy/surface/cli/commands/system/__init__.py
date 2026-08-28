"""Workspace-scoped System command tree."""

from __future__ import annotations

from typing import Any

import typer

from kairospy.system.apps.launch.application import (
    WorkspaceComponentDependencyApplication,
)
from kairospy.system.apps.components.application import (
    CapitalSystemClient,
    NativeCliApplication,
    RiskSystemClient,
)
from kairospy.surface.cli.options import OutputFormat, render


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


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
            str(owner.paths.snapshots),
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


from . import lifecycle as _lifecycle  # noqa: E402,F401
from . import market as _market  # noqa: E402,F401
from . import risk as _risk  # noqa: E402,F401
from . import capital as _capital  # noqa: E402,F401
from . import reference as _reference  # noqa: E402,F401
