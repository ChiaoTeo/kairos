"""Account-scoped standalone Execution order workflow."""

from __future__ import annotations

import json

from prettytable import PrettyTable
import typer

from kairospy.application.account.cli import AccountCliApplication
from kairospy.application.system import NativeCliApplication

from ...models import GuidedCommand, InteractiveContext, ShellAction, ShellControl


def print_menu(context: InteractiveContext) -> None:
    account_id = _account_id(context)
    path = context.shell_path
    if len(path) == 5:
        leaf = path[4]
        if leaf in {"open", "history", "fills"}:
            label = {"open": "未完成订单", "history": "历史订单", "fills": "成交记录"}[
                leaf
            ]
            actions = "  r. 刷新"
            if leaf == "open":
                actions += "\n  s. 从列表选择订单"
            typer.echo(f"{label} · {account_id}\n{actions}")
            return
        typer.echo(
            "\n".join(
                (
                    f"订单：{leaf}",
                    f"账户：{account_id} · scope=direct-provider",
                    "  1. 查看详情",
                    "  2. 刷新交易所状态",
                    "  3. 撤单",
                    "  4. 修改订单",
                )
            )
        )
        return
    typer.echo(
        "\n".join(
            (
                f"订单管理 · {account_id} · 直接连接交易所：",
                "  1. 未完成订单",
                "  2. 历史订单",
                "  3. 成交记录",
                "  4. 查询订单",
                "  5. 下单",
                "  6. 撤单",
                "  7. 修改订单",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    if len(context.shell_path) == 5:
        typer.echo("可用命令：refresh/details/cancel/replace/back/home/exit")
        return
    typer.echo(
        "可用命令：open-orders/history/fills/order/submit/cancel/replace/back/home/exit"
    )


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if len(parts) != 1:
        return None
    account_id = _account_id(context)
    segment = context.selected_account_segment
    if not account_id:
        typer.echo("请先从 /trade/accounts 选择账户。")
        return ShellControl.HANDLED
    key = parts[0]
    path = context.shell_path
    if len(path) == 5:
        leaf = path[4]
        if leaf in {"open", "history", "fills"}:
            if leaf == "open" and key in {"s", "select"}:
                return _select_open_order(context, account_id)
            if key not in {"r", "refresh"}:
                return None
            return _list_command(
                account_id, leaf, segment=segment, symbol=context.selected_order_symbol
            )
        context.selected_order = leaf
        if key in {"1", "details", "show", "status", "r", "refresh"}:
            return _order_command(
                account_id, leaf, segment=segment, symbol=context.selected_order_symbol
            )
        if key in {"3", "cancel"}:
            return _cancel_command(context, account_id, leaf, segment=segment)
        if key in {"4", "replace"}:
            return _replace_command(context, account_id, leaf, segment=segment)
        return None

    if key in {"1", "open", "open-orders"}:
        context.selected_order_symbol = None
        context.shell_path = (*path, "open")
        return _list_command(account_id, "open", segment=segment)
    if key in {"2", "history"}:
        context.selected_order_symbol = _prompt_query_symbol(context)
        context.shell_path = (*path, "history")
        return _list_command(
            account_id, "history", segment=segment, symbol=context.selected_order_symbol
        )
    if key in {"3", "fills"}:
        context.selected_order_symbol = _prompt_query_symbol(context)
        context.shell_path = (*path, "fills")
        return _list_command(
            account_id, "fills", segment=segment, symbol=context.selected_order_symbol
        )
    if key in {"4", "order", "show", "status"}:
        order_id = _prompt_order_id()
        context.selected_order_symbol = _prompt_order_symbol(context)
        context.selected_order = order_id
        context.shell_path = (*path, order_id)
        return _order_command(
            account_id, order_id, segment=segment, symbol=context.selected_order_symbol
        )
    if key in {"5", "submit", "place"}:
        return _submit_command(context, account_id, segment=segment)
    if key in {"6", "cancel"}:
        return _cancel_command(context, account_id, _prompt_order_id(), segment=segment)
    if key in {"7", "replace"}:
        return _replace_command(
            context, account_id, _prompt_order_id(), segment=segment
        )
    return None


def _account_id(context: InteractiveContext) -> str:
    return context.selected_account or (
        context.shell_path[2]
        if len(context.shell_path) >= 3
        and context.shell_path[:2] == ("trade", "accounts")
        else ""
    )


def _list_command(
    account_id: str,
    view: str,
    *,
    segment: str | None = None,
    symbol: str | None = None,
) -> GuidedCommand:
    action = {"open": "open-orders", "history": "history", "fills": "fills"}[view]
    argv = ("order", action, "--account-id", account_id)
    if segment:
        argv = (*argv, "--segment", segment)
    if symbol:
        argv = (*argv, "--symbol", symbol)
    return GuidedCommand(
        (*argv, "--format", "table"),
        f"直接查询账户 {account_id} 的{ {'open': '未完成订单', 'history': '历史订单', 'fills': '成交记录'}[view] }",
    )


def _order_command(
    account_id: str,
    order_id: str,
    *,
    segment: str | None = None,
    symbol: str | None = None,
) -> GuidedCommand:
    argv = (
        "order",
        "order",
        "--account-id",
        account_id,
        "--order-id",
        order_id,
    )
    if segment:
        argv = (*argv, "--segment", segment)
    if symbol:
        argv = (*argv, "--symbol", symbol)
    return GuidedCommand(
        (*argv, "--format", "table"),
        f"直接查询交易所订单 {order_id}",
    )


def _submit_command(
    context: InteractiveContext, account_id: str, *, segment: str | None = None
) -> GuidedCommand:
    order_id = typer.prompt("order id").strip()
    instrument_id = typer.prompt("instrument id").strip()
    symbol = typer.prompt("provider symbol", default=instrument_id).strip()
    quantity = typer.prompt("quantity").strip()
    side = typer.prompt("side", default="buy").strip()
    order_type = typer.prompt("order type", default="market").strip()
    argv = (
        "order",
        "submit",
        "--account-id",
        account_id,
        "--order-id",
        order_id,
        "--instrument-id",
        instrument_id,
        "--symbol",
        symbol,
        "--quantity",
        quantity,
        "--side",
        side,
        "--order-type",
        order_type,
    )
    if segment:
        argv = (*argv, "--segment", segment)
    if order_type == "limit":
        limit_price = typer.prompt("limit price").strip()
        argv = (*argv, "--limit-price", limit_price)
    return GuidedCommand(
        (*argv, "--yes", "--format", "json"),
        f"{_scope_summary(context, account_id)} · 提交订单 {order_id}",
        dangerous=True,
    )


def _cancel_command(
    context: InteractiveContext,
    account_id: str,
    order_id: str,
    *,
    segment: str | None = None,
) -> GuidedCommand:
    argv = (
        "order",
        "cancel",
        "--account-id",
        account_id,
        "--order-id",
        order_id,
    )
    if segment:
        argv = (*argv, "--segment", segment)
    return GuidedCommand(
        (*argv, "--yes", "--format", "json"),
        f"{_scope_summary(context, account_id)} · 撤销订单 {order_id}",
        dangerous=True,
    )


def _replace_command(
    context: InteractiveContext,
    account_id: str,
    order_id: str,
    *,
    segment: str | None = None,
) -> GuidedCommand:
    replacement_order_id = typer.prompt("replacement order id").strip()
    instrument_id = typer.prompt("instrument id").strip()
    symbol = typer.prompt("provider symbol（可留空）", default="").strip()
    quantity = typer.prompt("new quantity").strip()
    limit_price = typer.prompt("new limit price（可留空）", default="").strip()
    argv = (
        "order",
        "replace",
        "--account-id",
        account_id,
        "--target-order-id",
        order_id,
        "--order-id",
        replacement_order_id,
        "--instrument-id",
        instrument_id,
        "--quantity",
        quantity,
    )
    if segment:
        argv = (*argv, "--segment", segment)
    if symbol:
        argv = (*argv, "--symbol", symbol)
    if limit_price:
        argv = (*argv, "--limit-price", limit_price)
    return GuidedCommand(
        (*argv, "--yes", "--format", "json"),
        f"{_scope_summary(context, account_id)} · 修改订单 {order_id}",
        dangerous=True,
    )


def _prompt_order_id() -> str:
    order_id = typer.prompt("order id").strip()
    if not order_id:
        raise typer.BadParameter("order id 不能为空")
    return order_id


def _scope_summary(context: InteractiveContext, account_id: str) -> str:
    return (
        f"account={account_id} · provider={context.selected_account_provider or 'unknown'} · "
        f"environment={context.selected_account_environment or 'unknown'} · "
        f"segment={context.selected_account_segment or 'unknown'} · scope=direct-provider"
    )


def _prompt_query_symbol(context: InteractiveContext) -> str | None:
    required = (context.selected_account_provider or "").lower() == "binance"
    symbol = typer.prompt(
        "provider symbol" + ("" if required else "（可留空）"),
        default="BTCUSDT" if required else "",
    ).strip()
    if required and not symbol:
        raise typer.BadParameter("Binance 历史订单和成交查询需要 provider symbol")
    return symbol or None


def _prompt_order_symbol(context: InteractiveContext) -> str | None:
    required = (context.selected_account_provider or "").lower() == "binance"
    if not required:
        return None
    symbol = typer.prompt(
        "provider symbol",
        default="BTCUSDT",
    ).strip()
    if required and not symbol:
        raise typer.BadParameter("Binance 订单查询需要 provider symbol")
    return symbol


def _select_open_order(context: InteractiveContext, account_id: str) -> ShellAction:
    if context.owner is None:
        typer.echo("当前没有可用 workspace。")
        return ShellControl.HANDLED
    try:
        binding_args = [
            "standalone",
            "trading-binding",
            "--account-id",
            account_id,
            "--access",
            "read",
        ]
        if context.selected_account_segment:
            binding_args.extend(("--segment", context.selected_account_segment))
        binding = AccountCliApplication(context.owner).run(binding_args)
        result = NativeCliApplication(context.owner).invoke(
            "execution",
            [
                "standalone",
                "--binding-json",
                json.dumps(binding),
                "open-orders",
                "--output",
                "json",
            ],
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        value = json.loads(result.stdout)
        orders = value.get("orders", ()) if isinstance(value, dict) else ()
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        typer.echo(f"读取未完成订单失败：{error}")
        return ShellControl.HANDLED
    if not orders:
        typer.echo("当前没有未完成订单。")
        return ShellControl.HANDLED
    table = PrettyTable(["序号", "订单", "交易所订单", "symbol", "side", "status"])
    table.align = "l"
    for index, order in enumerate(orders, start=1):
        table.add_row(
            [
                index,
                order.get("order_id", "-"),
                order.get("remote_order_id", "-"),
                order.get("symbol", "-"),
                order.get("side", "-"),
                order.get("status", "-"),
            ]
        )
    typer.echo(table)
    selected = typer.prompt("选择订单序号", default="1").strip()
    if not selected.isdigit() or not 1 <= int(selected) <= len(orders):
        typer.echo(f"找不到订单序号：{selected}")
        return ShellControl.HANDLED
    order = orders[int(selected) - 1]
    order_id = str(order.get("order_id") or order.get("remote_order_id") or "").strip()
    if not order_id:
        typer.echo("选中的订单没有可用标识。")
        return ShellControl.HANDLED
    context.selected_order = order_id
    context.selected_order_symbol = str(order.get("symbol") or "").strip() or None
    context.shell_path = (*context.shell_path[:4], order_id)
    return _order_command(
        account_id,
        order_id,
        segment=context.selected_account_segment,
        symbol=context.selected_order_symbol,
    )


def choose() -> GuidedCommand:
    account_id = typer.prompt("账户 id").strip()
    return _list_command(account_id, "open")
