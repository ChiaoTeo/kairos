"""Workspace context, navigation state, and global summary rendering."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import typer

from kairospy.application.system import ComponentProcessApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.console.data import SystemObserveReader
from kairospy.surface.console.models import ObserveSnapshot

from .models import InteractiveContext


def create_context(workspace: Path | None) -> InteractiveContext:
    owner = resolve_workspace(workspace)
    return InteractiveContext(
        owner=owner,
        snapshot=read_snapshot(owner) if owner is not None else None,
        workspace_arg=workspace,
    )


def refresh_context(context: InteractiveContext) -> None:
    context.owner = resolve_workspace(context.workspace_arg)
    context.snapshot = (
        read_snapshot(context.owner) if context.owner is not None else None
    )


def go_home(context: InteractiveContext) -> None:
    context.shell_path = ()
    context.selected_account = None
    context.selected_account_provider = None
    context.selected_account_environment = None
    context.selected_account_segment = None
    context.selected_launch = None
    context.selected_launch_mode = None
    context.selected_launch_instance = None
    context.selected_service = None
    context.selected_order = None
    context.selected_order_symbol = None
    context.selected_market = None
    context.selected_market_source = None
    context.selected_reference = None
    context.selected_reference_kind = None


def go_back(context: InteractiveContext) -> None:
    previous = context.shell_path
    previous_section = next(iter(previous), None)
    leaving_market = previous in {("market",), ("system", "market")} or (
        len(previous) >= 6
        and previous_section == "launch"
        and previous[-2:] == ("components", "market")
    )
    context.shell_path = previous[:-1]
    if previous[:2] == ("trade", "accounts"):
        if len(previous) == 2:
            context.shell_path = ("trade",)
            context.selected_account = None
            context.selected_account_provider = None
            context.selected_account_environment = None
            context.selected_account_segment = None
            context.selected_order = None
            context.selected_order_symbol = None
            context.selected_market = None
            context.selected_market_source = None
        elif len(previous) == 3:
            context.shell_path = ("trade", "accounts")
            context.selected_account = None
            context.selected_account_provider = None
            context.selected_account_environment = None
            context.selected_account_segment = None
            context.selected_order = None
            context.selected_order_symbol = None
            context.selected_market = None
            context.selected_market_source = None
        elif len(previous) >= 4 and previous[3] == "orders":
            if len(previous) == 4:
                context.shell_path = previous[:3]
                context.selected_account_segment = None
                context.selected_order = None
                context.selected_order_symbol = None
            elif len(previous) == 5:
                context.shell_path = previous[:4]
                context.selected_order = None
                context.selected_order_symbol = None
    if not context.shell_path:
        context.selected_account = None
        context.selected_account_provider = None
        context.selected_account_environment = None
        context.selected_account_segment = None
        context.selected_order = None
        context.selected_order_symbol = None
    if context.shell_path == ("trade",):
        context.selected_account = None
        context.selected_account_provider = None
        context.selected_account_environment = None
        context.selected_account_segment = None
        context.selected_order = None
        context.selected_order_symbol = None
    if not context.shell_path or context.shell_path == ("launch",):
        context.selected_launch = None
        context.selected_launch_mode = None
        context.selected_launch_instance = None
    elif (
        context.shell_path[:1] == ("launch",)
        and len(context.shell_path) == 3
        and context.shell_path[2] == "instances"
    ):
        context.selected_launch_mode = None
        context.selected_launch_instance = None
    if not context.shell_path or context.shell_path == ("system",):
        context.selected_service = None
    if leaving_market:
        context.selected_market = None
        context.selected_market_source = None
    if previous_section == "reference" and len(previous) > 1:
        context.selected_reference = None
        context.selected_reference_kind = None


def resolve_workspace(workspace: Path | None) -> Any | None:
    try:
        return WorkspaceApplication().resolve(workspace)
    except (FileNotFoundError, ValueError) as error:
        typer.echo("当前没有识别到 Kairos 项目。")
        typer.echo(f"原因：{error}")
        typer.echo("你仍然可以选择“从零开始”来创建项目。")
        typer.echo()
        return None


def read_snapshot(owner: Any) -> ObserveSnapshot | None:
    try:
        return SystemObserveReader(
            ComponentProcessApplication(owner), owner.workspace_id
        ).read()
    except Exception as error:
        typer.echo(f"读取 workspace 状态失败：{error}")
        return None


def unique_launches(snapshot: ObserveSnapshot) -> tuple[dict[str, object], ...]:
    values: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for item in snapshot.launches:
        launch_id = str(item.get("launch_id") or "").strip()
        mode = str(item.get("mode") or "").strip()
        key = (launch_id, mode)
        if not launch_id or key in seen:
            continue
        seen.add(key)
        values.append(dict(item))
    return tuple(values)


def print_context(
    context: InteractiveContext,
    accounts: Sequence[dict[str, Any]] = (),
) -> None:
    owner = context.owner
    snapshot = context.snapshot
    if owner is None:
        return
    typer.echo(f"Workspace: {owner.workspace_id} · {owner.paths.project_root}")
    account_issues = sum(
        account.get("status") not in {"configured", "connected", "ready"}
        for account in accounts
    )
    if snapshot is None:
        typer.echo(
            f"{len(accounts)} 个账户 · {account_issues} 个配置异常 · 运行状态暂不可用"
        )
        typer.echo()
        return
    failed_launches = sum(
        str(value.get("state")) == "failed" for value in unique_launches(snapshot)
    )
    unavailable_services = sum(
        snapshot.components.get(name, {}).get("status")
        not in {"ok", "ready", "running", "degraded"}
        for name in ("reference", "market")
    )
    typer.echo(
        f"{len(accounts)} 个账户 · {account_issues} 个配置异常 · "
        f"{failed_launches} 个策略失败 · {unavailable_services} 个系统服务未运行"
    )
    if context.selected_account or context.selected_launch:
        typer.echo(
            f"当前：account={context.selected_account or '—'} · "
            f"strategy={context.selected_launch or '—'}"
        )
    if context.last_command is not None:
        typer.echo(
            f"上次：status={context.last_status if context.last_status is not None else '—'} · "
            f"{context.last_command}"
        )
    typer.echo()
