"""Workspace context, navigation state, and global summary rendering."""

from __future__ import annotations

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
        workspace_arg=(
            workspace
            if workspace is not None
            else owner.paths.root
            if owner is not None
            else None
        ),
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
    context.selected_market_provider = None
    context.selected_reference = None
    context.selected_reference_kind = None


def go_back(context: InteractiveContext) -> None:
    previous = context.shell_path
    previous_section = next(iter(previous), None)
    leaving_market = (
        previous_section == "market"
        or previous == ("system", "market")
        or (
            len(previous) >= 6
            and previous_section == "launch"
            and previous[-2:] == ("components", "market")
        )
    )
    context.shell_path = previous[:-1]
    if previous[:2] in {("trade", "accounts"), ("resources", "accounts")}:
        account_parent = previous[:2]
        if len(previous) == 2:
            context.shell_path = previous[:1]
            context.selected_account = None
            context.selected_account_provider = None
            context.selected_account_environment = None
            context.selected_account_segment = None
            context.selected_order = None
            context.selected_order_symbol = None
            context.selected_market = None
            context.selected_market_provider = None
        elif len(previous) == 3:
            context.shell_path = account_parent
            context.selected_account = None
            context.selected_account_provider = None
            context.selected_account_environment = None
            context.selected_account_segment = None
            context.selected_order = None
            context.selected_order_symbol = None
            context.selected_market = None
            context.selected_market_provider = None
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
        context.selected_market_provider = None
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


def print_context(context: InteractiveContext) -> None:
    """Render the quiet, contextual header shown above the product home."""

    owner = context.owner
    snapshot = context.snapshot
    if owner is None:
        return
    typer.echo(f"Kairos  ·  {owner.workspace_id}")
    typer.echo(str(owner.paths.project_root))
    if snapshot is None:
        typer.echo()
        typer.echo("注意  暂时无法读取运行状态，输入 6 检查系统状态")
        typer.echo()
        return
    launches = unique_launches(snapshot)
    active_states = {"starting", "running", "degraded", "stopping"}
    running_launches = sum(
        str(value.get("state")).lower() in active_states for value in launches
    )
    unavailable_services = (
        sum(
            snapshot.components.get(name, {}).get("status")
            not in {"ok", "ready", "running", "degraded"}
            for name in ("reference", "market")
        )
        if running_launches
        else 0
    )
    if running_launches:
        typer.echo()
        typer.echo(f"运行  {running_launches} 个策略正在运行")
    if unavailable_services:
        typer.echo(
            f"注意  {unavailable_services} 个运行所需服务当前不可用，输入 fix 检查"
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


def print_home_header(context: InteractiveContext) -> None:
    """Render the framed workspace region at the top of the home page."""

    owner = context.owner
    snapshot = context.snapshot
    if owner is None:
        typer.echo("╭─ Kairos")
        typer.echo("│  未选择工作区")
        return
    typer.echo(f"╭─ Kairos  ·  {owner.workspace_id}")
    typer.echo(f"│  {owner.paths.project_root}")
    if snapshot is None:
        typer.echo("│  注意：暂时无法读取运行状态，输入 6 检查系统状态")
        return
    launches = unique_launches(snapshot)
    active_states = {"starting", "running", "degraded", "stopping"}
    running_launches = sum(
        str(value.get("state")).lower() in active_states for value in launches
    )
    if not running_launches:
        return
    unavailable_services = sum(
        snapshot.components.get(name, {}).get("status")
        not in {"ok", "ready", "running", "degraded"}
        for name in ("reference", "market")
    )
    typer.echo(f"│  运行：{running_launches} 个策略正在运行")
    if unavailable_services:
        typer.echo(
            f"│  注意：{unavailable_services} 个运行所需服务不可用，输入 fix 检查"
        )
