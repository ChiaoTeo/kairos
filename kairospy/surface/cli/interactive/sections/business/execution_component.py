"""Launch-instance-scoped connected Execution component workflow."""

from __future__ import annotations

import typer

from kairospy.application.system import NativeCliApplication

from ...models import GuidedCommand, InteractiveContext
from . import account as account_section


_ACTIONS = {
    "1": "status",
    "status": "status",
    "2": "snapshot",
    "snapshot": "snapshot",
    "3": "routes",
    "routes": "routes",
    "4": "orders",
    "orders": "orders",
    "5": "open-orders",
    "open-orders": "open-orders",
    "6": "history",
    "history": "history",
    "7": "fills",
    "fills": "fills",
    "8": "events",
    "events": "events",
    "9": "audit",
    "audit": "audit",
    "10": "inspect",
    "inspect": "inspect",
    "11": "trace",
    "trace": "trace",
    "12": "journal",
    "journal": "journal",
    "13": "reconcile",
    "reconcile": "reconcile",
    "14": "submit",
    "submit": "submit",
    "15": "cancel",
    "cancel": "cancel",
    "16": "replace",
    "replace": "replace",
}


def print_menu(context: InteractiveContext) -> None:
    launch_id, instance_id, mode = _identity(context)
    typer.echo(
        "\n".join(
            (
                f"Execution Server · {launch_id}/{instance_id} · mode={mode}：",
                "  1. 服务状态",
                "  2. 运行时快照",
                "  3. 执行路由",
                "  4. 全部订单",
                "  5. 未完成订单",
                "  6. 历史订单",
                "  7. 成交记录",
                "  8. 生命周期事件",
                "  9. 审计记录",
                "  10. 检查订单",
                "  11. 追踪订单",
                "  12. 订单 journal",
                "  13. 请求对账",
                "  14. 提交订单",
                "  15. 撤销订单",
                "  16. 修改订单",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo(
        "可用命令：status/snapshot/routes/orders/open-orders/history/fills/events/"
        "audit/inspect/trace/journal/reconcile/submit/cancel/replace/back/home/exit"
    )


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> GuidedCommand | None:
    if len(parts) != 1:
        return None
    action = _ACTIONS.get(parts[0])
    if action is None:
        return None
    launch_id, instance_id, mode = _identity(context)
    argv = (
        "launch",
        "instance",
        "component",
        "execution",
        action,
        launch_id,
        "--instance",
        instance_id,
        "--mode",
        mode,
    )
    account_id: str | None = None
    if action in {"inspect", "trace", "journal", "cancel", "replace"}:
        order_id = typer.prompt("order id").strip()
        if not order_id:
            raise typer.BadParameter("order id 不能为空")
        argv = (*argv, "--order-id", order_id)
        if action in {"cancel", "replace"}:
            account_id = _connected_order_account(context, order_id)
    if action == "cancel":
        reason = typer.prompt("撤单原因", default="manual cancel").strip()
        argv = (*argv, "--reason", reason)
    if action == "replace":
        quantity = typer.prompt("new quantity").strip()
        argv = (*argv, "--quantity", quantity)
        limit_price = typer.prompt("new limit price（可留空）", default="").strip()
        if limit_price:
            argv = (*argv, "--limit-price", limit_price)
    if action == "submit":
        submit_arguments, account_id = _submit_arguments()
        argv = (*argv, *submit_arguments)
    dangerous = action in {"submit", "cancel", "replace", "reconcile"}
    summary = f"操作 {launch_id}/{instance_id} 的 Execution Server：{action}"
    if dangerous:
        summary = _connected_scope_summary(
            context,
            launch_id=launch_id,
            instance_id=instance_id,
            mode=mode,
            account_id=account_id or "all",
            action=action,
        )
    return GuidedCommand(
        (
            *argv,
            "--format",
            "json"
            if action in {"submit", "cancel", "replace", "reconcile"}
            else "table",
        ),
        summary,
        dangerous=dangerous,
    )


def _identity(context: InteractiveContext) -> tuple[str, str, str]:
    launch_id = context.selected_launch or (
        context.shell_path[1] if len(context.shell_path) > 1 else ""
    )
    instance_id = context.selected_launch_instance or (
        context.shell_path[3] if len(context.shell_path) > 3 else ""
    )
    mode = context.selected_launch_mode or ""
    if not launch_id or not instance_id or not mode:
        raise typer.BadParameter("必须先选择具体 launch instance")
    return launch_id, instance_id, mode


def _submit_arguments() -> tuple[tuple[str, ...], str]:
    order_id = typer.prompt("order id").strip()
    account_id = typer.prompt("account id").strip()
    segment_key = typer.prompt("segment key", default="spot").strip()
    instrument_id = typer.prompt("instrument id").strip()
    quantity = typer.prompt("quantity").strip()
    route_id = typer.prompt("execution route id").strip()
    side = typer.prompt("side", default="buy").strip()
    order_type = typer.prompt("order type", default="market").strip()
    argv = (
        "--order-id",
        order_id,
        "--account-id",
        account_id,
        "--segment-key",
        segment_key,
        "--instrument-id",
        instrument_id,
        "--quantity",
        quantity,
        "--execution-route-id",
        route_id,
        "--side",
        side,
        "--order-type",
        order_type,
    )
    if order_type == "limit":
        argv = (*argv, "--limit-price", typer.prompt("limit price").strip())
    return argv, account_id


def _connected_order_account(context: InteractiveContext, order_id: str) -> str:
    if context.owner is None:
        raise typer.BadParameter("当前没有可用 workspace，无法确认订单账户")
    launch_id, instance_id, mode = _identity(context)
    try:
        value = NativeCliApplication(context.owner).run(
            "execution",
            [
                "connected",
                "--mode",
                mode,
                "--launch-id",
                launch_id,
                "--instance-id",
                instance_id,
                "inspect",
                "--order-id",
                order_id,
            ],
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise typer.BadParameter(
            f"无法从 Execution Server 解析订单账户：{error}"
        ) from error
    account_id = str(value.get("account_id") or "").strip()
    if not account_id:
        raise typer.BadParameter(f"Execution 订单 {order_id} 缺少 account_id")
    return account_id


def _connected_scope_summary(
    context: InteractiveContext,
    *,
    launch_id: str,
    instance_id: str,
    mode: str,
    account_id: str,
    action: str,
) -> str:
    account = next(
        (
            value
            for value in account_section.records(context)
            if str(value.get("account_id") or "") == account_id
        ),
        {},
    )
    if account_id != "all" and not account:
        raise typer.BadParameter(
            f"无法从 Account owner 解析账户 {account_id} 的 provider 和 environment"
        )
    provider = (
        account.get("integration_provider")
        or account.get("exchange")
        or account.get("broker")
        or ("runtime-managed" if account_id == "all" else "unknown")
    )
    environment = account.get("environment") or mode
    return (
        f"launch={launch_id} · instance={instance_id} · mode={mode} · "
        f"account={account_id} · provider={provider} · environment={environment} · "
        f"scope=launch-instance · {action}"
    )
