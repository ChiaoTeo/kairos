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
    leaving_market = previous_section == "market" or previous == ("system", "market") or (
        len(previous) >= 6
        and previous_section == "launch"
        and previous[-2:] == ("components", "market")
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


def _launch_readiness(owner: Any) -> tuple[int, int]:
    """Count launch working copies without conflating them with runtime state."""

    try:
        from kairospy.application.launch.application import (
            LaunchConfigurationApplication,
        )

        root = Path(owner.paths.root)
        launch_root = root / "config" / "launches"
        selected = {
            path.stem: path
            for path in sorted(launch_root.glob("*.toml"))
            if path.is_file()
        }
        draft_root = launch_root / ".drafts"
        for path in sorted(draft_root.glob("*.toml")) if draft_root.is_dir() else ():
            selected[path.stem] = path
        application = LaunchConfigurationApplication()
        ready = 0
        for launch_id, path in selected.items():
            try:
                report = application.validate(path, workspace_root=root)
                has_return_point = (
                    path.parent == draft_root
                    and application.draft_return(root, launch_id) is not None
                )
                ready += bool(report["valid"]) and not has_return_point
            except (OSError, TypeError, ValueError):
                # A malformed working copy is a blocked Launch, not a broken home page.
                continue
        return ready, len(selected) - ready
    except (AttributeError, OSError, TypeError, ValueError):
        return 0, 0


def _resource_readiness(
    owner: Any, accounts: Sequence[dict[str, Any]]
) -> tuple[int, int]:
    """Count configured resources by their retained manual-test evidence."""

    resources = list(accounts)
    try:
        from kairospy.application.agent import AgentResourceApplication
        from kairospy.application.notification import NotificationAdminApplication
        from kairospy.application.reference import (
            ReferenceProviderConfigurationApplication,
        )

        resources.extend(ReferenceProviderConfigurationApplication(owner).list())
        resources.extend(AgentResourceApplication(owner).model_connections())
        resources.extend(NotificationAdminApplication(owner).list())
    except (AttributeError, OSError, TypeError, ValueError):
        # Keep the Account facts already supplied by the interactive session.
        pass
    verified = sum(
        value.get("verification_status") == "verified" for value in resources
    )
    return verified, len(resources) - verified


def print_context(
    context: InteractiveContext,
    accounts: Sequence[dict[str, Any]] = (),
) -> None:
    owner = context.owner
    snapshot = context.snapshot
    if owner is None:
        return
    typer.echo(f"工作区  {owner.workspace_id}")
    typer.echo(f"        {owner.paths.project_root}")
    ready_launches, blocked_launches = _launch_readiness(owner)
    verified_resources, pending_resources = _resource_readiness(owner, accounts)
    typer.echo(
        f"运行准备  {ready_launches} 个 Launch 可启动 · "
        f"{blocked_launches} 个需要处理"
    )
    typer.echo(
        f"运行资源  {verified_resources} 个已验证 · {pending_resources} 个待处理"
    )
    if snapshot is None:
        typer.echo("正在运行  状态暂不可用")
        typer.echo("下一步    输入 6 检查系统状态")
        typer.echo()
        return
    launches = unique_launches(snapshot)
    failed_launches = sum(str(value.get("state")) == "failed" for value in launches)
    completed_launches = sum(
        str(value.get("state")).lower() == "completed" for value in launches
    )
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
    typer.echo(
        f"正在运行  {running_launches} 个策略 · "
        f"{unavailable_services} 个必需服务不可用"
    )
    typer.echo(
        f"最近结果  {failed_launches} 个策略失败 · {completed_launches} 个策略完成"
    )
    suggestions: list[str] = []
    if unavailable_services:
        suggestions.append("输入 fix 修复运行依赖")
    if blocked_launches:
        suggestions.append("输入 launch 处理 Launch 配置")
    if pending_resources:
        suggestions.append("输入 resources 检查运行资源")
    if failed_launches:
        suggestions.append("输入 diagnose 排查最近失败")
    if suggestions:
        typer.echo(f"下一步    {'；'.join(suggestions)}")
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
