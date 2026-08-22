"""Interactive order-facing surface owned by Execution."""

from __future__ import annotations

import typer

from ...models import GuidedCommand, InteractiveContext


def print_menu(context: InteractiveContext) -> None:
    del context
    typer.echo(
        "\n".join(
            (
                "Execution 订单工具：",
                "  1. 追踪运行中订单",
                "  2. 审计本地 evidence",
                "  3. 检查订单",
                "  4. 查看 journal",
                "  5. 查看 fills",
                "  6. 预览提交订单",
                "  7. 运行时快照",
                "  8. 执行路由",
                "  9. 全部订单",
                "  10. 未完成订单",
                "  11. 历史订单",
                "  12. 成交记录",
                "  13. 生命周期事件",
                "  14. 审计记录",
                "  15. 检查订单",
                "  16. 追踪订单",
                "  17. 订单 journal",
                "  18. 请求对账",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo(
        "可用命令：status/audit/inspect/journal/fills/preview/snapshot/routes/"
        "orders/open-orders/history/events/trace/reconcile"
    )


def handle(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    del context
    if len(parts) != 1:
        return None
    key = parts[0]
    if key in {"1", "status"}:
        launch_id = typer.prompt("launch id", default="demo-backtest").strip()
        instance_id = typer.prompt("instance id", default="current").strip()
        order_id = typer.prompt("order id").strip()
        if not order_id:
            raise typer.BadParameter("order id 不能为空")
        return GuidedCommand(
            (
                "launch", "instance", "component", "execution", "trace",
                launch_id, "--instance", instance_id, "--order-id", order_id,
                "--format", "table",
            ),
            "追踪运行中订单",
        )
    if key in {"2", "audit", "3", "inspect", "4", "journal", "5", "fills"}:
        action = {"2": "audit", "3": "inspect", "4": "journal", "5": "fills"}.get(key, key)
        evidence = typer.prompt("execution evidence 文件", default="execution-evidence.json").strip()
        argv = ("order", action, "--file", evidence)
        if action in {"inspect", "journal"}:
            order_id = typer.prompt("order id").strip()
            argv = (*argv, "--order-id", order_id)
        return GuidedCommand(argv, f"读取 Execution {action} evidence")
    if key in {"6", "preview"}:
        request = typer.prompt("submit-order.json 路径", default="submit-order.json").strip()
        return GuidedCommand(("order", "preview-submit-file", "--file", request), "预览订单提交")
    connected = {
        "7": "snapshot", "snapshot": "snapshot",
        "8": "routes", "routes": "routes",
        "9": "orders", "orders": "orders",
        "10": "open-orders", "open-orders": "open-orders",
        "11": "history", "history": "history",
        "12": "fills", "runtime-fills": "fills",
        "13": "events", "events": "events",
        "14": "audit", "runtime-audit": "audit",
        "15": "inspect", "runtime-inspect": "inspect",
        "16": "trace", "trace": "trace",
        "17": "journal", "runtime-journal": "journal",
        "18": "reconcile", "reconcile": "reconcile",
    }.get(key)
    if connected is not None:
        action = connected
        launch_id = typer.prompt("launch id", default="demo-backtest").strip()
        instance_id = typer.prompt("instance id", default="current").strip()
        argv = (
            "launch", "instance", "component", "execution", action,
            launch_id, "--instance", instance_id, "--format", "table",
        )
        if action in {"inspect", "trace", "journal"}:
            order_id = typer.prompt("order id").strip()
            argv = (*argv, "--order-id", order_id)
        return GuidedCommand(
            argv,
            f"查看运行中 Execution {action}" if action != "reconcile" else "请求 Execution 对账",
            dangerous=action == "reconcile",
        )
    return None


def choose() -> GuidedCommand:
    command = handle(
        InteractiveContext(None, None, None, shell_path=("order",)), ("status",)
    )
    assert command is not None
    return command
