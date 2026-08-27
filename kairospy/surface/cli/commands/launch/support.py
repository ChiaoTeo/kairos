"""Scoped adapters shared by launch-instance CLI commands."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
import sys
import time
from typing import Any

import typer
from prettytable import PrettyTable

from kairospy.surface.cli.options import OutputFormat, effective_output, render
from kairospy.system.apps.components.application import (
    InstanceSystemClients,
    NativeCliApplication,
    UnixRestClient,
)
from kairospy.system.apps.launch.application import (
    LaunchControlApplication,
    LaunchRuntimeApplication,
    LaunchRuntimeError,
)
from kairospy.system.apps.launch.application.connections import (
    resolve_instance_connections,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import instance_component_app


def _target(launch_id: str, instance: str, mode: str, workspace: Path):
    value = WorkspaceApplication().open(workspace)
    return LaunchControlApplication(value).target(launch_id, instance, mode=mode)


def _running_instance(owner, launch_id: str, mode: str | None = None) -> dict | None:
    try:
        return LaunchRuntimeApplication(owner).running_instance(launch_id, mode)
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error


def _resolve_launch_target(
    owner,
    launch_id: str,
    mode: str | None,
    instance: str | None,
) -> tuple[str, str]:
    try:
        return LaunchRuntimeApplication(owner).resolve_target(
            launch_id, mode=mode, instance=instance
        )
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error


def _decorate_launch_status(
    owner, launch_id: str, instance: str, mode: str, value: dict
) -> dict:
    return LaunchRuntimeApplication(owner).decorate_status(
        launch_id, instance, mode, value
    )


def _resolve_stop_instance(
    owner,
    launch_id: str,
    instance: str | None,
    mode: str | None,
) -> tuple[str, str]:
    try:
        return LaunchRuntimeApplication(owner).resolve_stop_target(
            launch_id, instance=instance, mode=mode
        )
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


def _emit_launch_account_balances(
    value: dict[str, object], output: OutputFormat
) -> None:
    if effective_output(output) is OutputFormat.JSON:
        _emit(value, output)
        return
    typer.echo(_render_launch_account_balances(value))


def _render_launch_account_balances(value: dict[str, object]) -> str:
    account_id = str(value["account_id"])
    balances = value.get("balances", [])
    if not isinstance(balances, list) or not balances:
        return f"No balances for account {account_id}."

    table = PrettyTable(["SEGMENT", "ASSET", "TOTAL", "AVAILABLE", "RESERVED"])
    table.align = "l"
    for balance in balances:
        if not isinstance(balance, dict):
            continue
        table.add_row(
            [
                balance.get("segment_key", balance.get("segment", "—")),
                balance.get("asset", "—"),
                balance.get("total", "—"),
                balance.get("available", "—"),
                balance.get("reserved", balance.get("locked", "—")),
            ]
        )
    context = (
        f"Account {account_id} · launch {value['launch_id']}/{value['instance_id']} "
        f"({value['mode']})"
    )
    return f"{context}\n{table}"


def _launch_config_path(owner, target: str | Path) -> Path:
    candidate = Path(target).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if candidate.is_file():
        return candidate
    configured = owner.paths.launch_config(str(target))
    if configured.is_file():
        return configured
    raise FileNotFoundError(
        f"launch {target!s} has no configuration; expected {configured}. "
        "Run 'kairos project doctor' to inspect project readiness."
    )


def _instance_account_snapshot(
    owner,
    *,
    launch_id: str,
    instance: str | None,
    account_id: str,
) -> tuple[dict[str, Any], str, str]:
    from kairospy.primitives.account import AccountId

    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    connections = resolve_instance_connections(instance_workspace)
    clients = InstanceSystemClients.from_connections(connections)
    account_key = AccountId(account_id)
    client = clients.accounts.get(account_key)
    if client is None:
        raise typer.BadParameter(
            f"launch instance has no connected account component for {account_id}"
        )
    snapshot = client.current_view(account_key).snapshot()
    return asdict(snapshot), resolved_instance, mode


def _instance_account_client(
    owner,
    *,
    launch_id: str,
    instance: str | None,
    account_id: str,
):
    from kairospy.primitives.account import AccountId

    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    connections = resolve_instance_connections(instance_workspace)
    clients = InstanceSystemClients.from_connections(connections)
    account_key = AccountId(account_id)
    client = clients.accounts.get(account_key)
    if client is None:
        raise typer.BadParameter(
            f"launch instance has no connected account component for {account_id}"
        )
    return client, resolved_instance, mode


def _run_account_connected_command(
    owner: Any,
    launch_id: str,
    instance: str | None,
    account_id: str,
    command: str,
    arguments: list[str],
) -> dict[str, Any]:
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    value = NativeCliApplication(owner).run(
        "account",
        [
            "--account-id",
            account_id,
            "--launch-id",
            launch_id,
            "--launch-mode",
            mode,
            "--instance-id",
            resolved_instance,
            "connected",
            command,
            *arguments,
        ],
    )
    value.setdefault("account_id", account_id)
    value.setdefault("launch_id", launch_id)
    value.setdefault("instance_id", resolved_instance)
    value.setdefault("mode", mode)
    value.setdefault("scope", "launch-instance")
    return value


def _run_instance_market_connected_command(
    owner: Any,
    *,
    launch_id: str,
    instance: str | None,
    command: str,
    arguments: list[str],
    require_views: bool,
) -> dict[str, Any]:
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    connections = resolve_instance_connections(instance_workspace)
    connection = connections.market
    if connection is None:
        raise typer.BadParameter(
            f"launch {launch_id} instance {resolved_instance} has no connected Market component"
        )
    if not connection.socket.exists():
        raise typer.BadParameter(
            f"无法连接 launch {launch_id} instance {resolved_instance} Market 服务。"
            "请先查看该 launch instance 的组件状态。"
        )
    target = ["--socket", str(connection.socket)]
    if connection.view_root is not None:
        target.extend(("--view-root", str(connection.view_root)))
    elif require_views:
        raise typer.BadParameter(
            f"launch {launch_id} instance {resolved_instance} Market connection "
            "has no view root"
        )
    value = NativeCliApplication(owner).run(
        "market", ["connected", command, *target, *arguments]
    )
    return {
        **value,
        "launch_id": launch_id,
        "instance_id": resolved_instance,
        "mode": mode,
        "scope": "launch-instance",
    }


def _run_execution_connected_command(
    owner: Any,
    launch_id: str,
    mode: str | None,
    instance: str | None,
    command: str,
    arguments: list[str],
) -> dict[str, Any]:
    resolved_instance, resolved_mode = _resolve_launch_target(
        owner, launch_id, mode, instance
    )
    value = NativeCliApplication(owner).run(
        "execution",
        [
            "connected",
            "--mode",
            resolved_mode,
            "--launch-id",
            launch_id,
            "--instance-id",
            resolved_instance,
            command,
            *arguments,
        ],
    )
    value.setdefault("launch_id", launch_id)
    value.setdefault("instance_id", resolved_instance)
    value.setdefault("mode", resolved_mode)
    value.setdefault("owner", "execution")
    value.setdefault("scope", "launch-instance")
    return value


def _execution_connected_passthrough(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None,
    mode: str | None,
    workspace: Path,
    output: OutputFormat,
    command: str,
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_execution_connected_command(
            owner,
            launch_id,
            mode,
            instance,
            command,
            list(ctx.args),
        ),
        output,
    )


_EXECUTION_PASSTHROUGH_CONTEXT = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
}


def _launch_instance_component_named_status(
    owner, launch_id: str, component: str, instance: str | None
) -> tuple[dict[str, object], str, str]:
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    statuses = LaunchRuntimeApplication(owner).component_status(instance_workspace)
    if component not in statuses:
        raise typer.BadParameter(
            f"launch instance has no connected {component} component"
        )
    return statuses[component], resolved_instance, mode


def _run_risk_connected_command(
    owner: Any,
    launch_id: str,
    instance: str | None,
    command: str,
    arguments: list[str],
) -> dict[str, Any]:
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    value = NativeCliApplication(instance_workspace).run(
        "risk",
        [
            "connected",
            command,
            *arguments,
        ],
    )
    value.setdefault("launch_id", launch_id)
    value.setdefault("instance_id", resolved_instance)
    value.setdefault("mode", mode)
    value.setdefault("scope", "launch-instance")
    return value


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


def _instance_capital_client(owner, launch_id: str, instance: str | None):
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    connections = resolve_instance_connections(instance_workspace)
    clients = InstanceSystemClients.from_connections(connections)
    if clients.capital is None:
        raise typer.BadParameter("launch instance has no connected capital component")
    return clients.capital, resolved_instance, mode


def _run_capital_connected_command(
    owner: Any,
    launch_id: str,
    instance: str | None,
    command: str,
    arguments: list[str],
) -> dict[str, Any]:
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    value = NativeCliApplication(instance_workspace).run(
        "capital",
        [
            "connected",
            command,
            *arguments,
        ],
    )
    value.setdefault("launch_id", launch_id)
    value.setdefault("instance_id", resolved_instance)
    value.setdefault("mode", mode)
    value.setdefault("scope", "launch-instance")
    return value


def _instance_reference_client(owner, launch_id: str, instance: str | None):
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    connections = resolve_instance_connections(instance_workspace)
    clients = InstanceSystemClients.from_connections(connections)
    if clients.reference is None:
        raise typer.BadParameter("launch instance has no connected reference component")
    return clients.reference.reader, resolved_instance, mode


@instance_component_app.command("status")
def launch_instance_component_status(
    component: str,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect one component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    statuses = LaunchRuntimeApplication(owner).component_status(instance_workspace)
    if component not in statuses:
        raise typer.BadParameter(
            f"launch instance has no connected component named {component}"
        )
    value = statuses[component]
    _emit(
        {
            **value,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )
