"""Interactive Account section."""

from __future__ import annotations

from typing import Any

from prettytable import PrettyTable
import typer

from kairospy.application.account.cli import AccountCliApplication

from ...models import GuidedCommand, InteractiveContext, ShellAction, ShellControl


def print_menu(context: InteractiveContext) -> None:
    if context.shell_path == ("account",):
        typer.echo("账户：")
        _print_account_list(context)
        typer.echo("输入序号选择并进入账户；refresh 刷新列表。")
        return
    account_id = context.selected_account or context.shell_path[-1]
    typer.echo(
        "\n".join(
            (
                f"当前账户：{account_id}",
                "  1. 账户概览",
                "  2. 资产与余额",
                "  3. 交易仓位",
                "  4. 理财与质押",
                "  5. 未完成订单",
                "  6. 费率与账户等级",
                "  7. 资金划转",
                "  8. 配置与凭据",
                "  9. 切换账户",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    if context.shell_path == ("account",):
        typer.echo("可用命令：<序号>/select/list/back/home/exit")
        return
    typer.echo(
        "可用命令：summary/assets/positions/earn/open-orders/fees/transfer/settings/switch"
    )


def choose(action: str | None = None) -> GuidedCommand:
    if action is None:
        action = _prompt_menu(
            "你想查询哪个账户信息？",
            (("1", "账户列表"), ("2", "账户余额"), ("3", "账户持仓")),
        )
    if action == "1":
        return GuidedCommand(("account", "list", "--output", "table"), "查看已配置账户")
    account = typer.prompt("账户 id", default="demo-paper").strip()
    command = "assets" if action == "2" else "positions"
    summary = "查询账户资产与余额" if action == "2" else "查询账户持仓"
    return build_fact_command(account, command, summary)


def _prompt_menu(title: str, choices: tuple[tuple[str, str], ...]) -> str:
    typer.echo(title)
    for key, label in choices:
        typer.echo(f"  {key}. {label}")
    valid = {key for key, _label in choices}
    while True:
        value = typer.prompt("请输入序号", default=choices[0][0]).strip()
        if value in valid:
            return value
        typer.echo("这个选项不存在，请重新输入。")


def handle(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if len(parts) != 1:
        return None
    key = parts[0]
    if context.shell_path == ("account",):
        accounts = records(context)
        if key.isdigit():
            index = int(key)
            if not 1 <= index <= len(accounts):
                typer.echo(f"找不到账户序号：{key}")
                return ShellControl.HANDLED
            _enter_account_context(context, accounts[index - 1], accounts=accounts)
            return ShellControl.HANDLED
        if key in {"select", "enter"}:
            _select_account(context)
            return ShellControl.HANDLED
        if key in {"list", "ls"}:
            _print_account_list(context, accounts=accounts)
            return ShellControl.HANDLED
        return None

    account_id = context.shell_path[1]
    context.selected_account = account_id
    if key in {"1", "summary", "overview"}:
        return account_fact_command(context, "overview", "查询账户概览")
    if key in {"2", "assets", "balances"}:
        return account_fact_command(context, "assets", "查询账户资产与余额")
    if key in {"3", "positions"}:
        return account_fact_command(context, "positions", "查询账户持仓")
    if key in {"4", "earn", "earn-holdings"}:
        return account_fact_command(context, "earn-holdings", "查询理财与质押持有")
    if key in {"5", "open-orders", "orders"}:
        return account_fact_command(context, "open-orders", "查询账户未完成订单")
    if key in {"6", "fees"}:
        scope = typer.prompt(
            "费率范围（产品:交易对，Binance 费率按交易对返回）",
            default="spot:BTCUSDT",
        ).strip()
        if ":" not in scope:
            raise typer.BadParameter("请使用 产品:交易对 格式，例如 spot:BTCUSDT")
        product, symbol = (part.strip() for part in scope.split(":", 1))
        if not product or not symbol:
            raise typer.BadParameter("产品和交易对不能为空")
        return GuidedCommand(
            (
                "account",
                "fees",
                account_id,
                "--product",
                product,
                "--symbol",
                symbol,
                "--format",
                "table",
            ),
            "查询指定产品和交易对的真实费率；直接回车使用 spot:BTCUSDT",
        )
    if key in {"7", "transfer"}:
        account = _selected_account_record(context)
        if "transfer" not in _account_capabilities(account):
            typer.echo("当前账户凭据不具备资金划转能力。")
            return ShellControl.HANDLED
        typer.echo("资金划转必须先 preview，再由用户确认执行；当前尚未开放执行。")
        return ShellControl.HANDLED
    if key in {"8", "settings", "configuration"}:
        return _account_settings_command(context)
    if key in {"9", "switch", "select"}:
        _select_account(context)
        return ShellControl.HANDLED
    return None


def account_fact_command(
    context: InteractiveContext, command: str, summary: str
) -> ShellAction:
    account_id = context.selected_account
    if account_id is None:
        typer.echo("请先选择账户。")
        return ShellControl.HANDLED
    return build_fact_command(account_id, command, summary)


def build_fact_command(
    account_id: str, command: str, summary: str
) -> GuidedCommand:
    return GuidedCommand(
        ("account", command, account_id, "--format", "table"), summary
    )


def _account_settings_command(context: InteractiveContext) -> GuidedCommand:
    account_id = context.selected_account or ""
    action = _prompt_menu(
        "账户配置与凭据：",
        (
            ("1", "查看账户配置"),
            ("2", "运行账户诊断"),
            ("3", "查看凭据列表"),
        ),
    )
    mapping = {
        "1": (("account", "show", "--account-id", account_id), "查看账户配置"),
        "2": (
            ("account", "doctor", "--account-id", account_id),
            "运行账户诊断",
        ),
        "3": (("account", "credential-list"), "查看凭据列表"),
    }
    argv, summary = mapping[action]
    return GuidedCommand((*argv, "--format", "text"), summary)


def _account_capabilities(account: dict[str, Any]) -> set[str]:
    capabilities = account.get("capabilities")
    if isinstance(capabilities, list):
        return {str(value) for value in capabilities}
    role = str(account.get("credential_role") or "readonly").lower()
    result = {"read"}
    if role in {"trade", "trading", "transfer", "admin"}:
        result.add("trade")
    if role in {"transfer", "admin"}:
        result.add("transfer")
    return result


def records(context: InteractiveContext) -> tuple[dict[str, Any], ...]:
    if context.owner is None:
        return ()
    try:
        value = AccountCliApplication(context.owner).run(["standalone", "list"])
    except (OSError, RuntimeError, ValueError) as error:
        typer.echo(f"读取账户列表失败：{error}")
        return ()
    accounts = value.get("accounts", ()) if isinstance(value, dict) else value
    if not isinstance(accounts, (list, tuple)):
        return ()
    return tuple(dict(account) for account in accounts if isinstance(account, dict))


def _select_account(context: InteractiveContext) -> None:
    accounts = records(context)
    if not accounts:
        typer.echo("当前 workspace 没有可选择的账户。")
        typer.echo("可先运行 kairos account simulate 或 kairos account register。")
        return
    _print_account_list(context, accounts=accounts)
    default = "1"
    if context.selected_account is not None:
        for index, account in enumerate(accounts, start=1):
            if account.get("account_id") == context.selected_account:
                default = str(index)
                break
    selected = typer.prompt(
        "选择账户序号或直接输入 account id", default=default
    ).strip()
    if selected in {"b", "back"}:
        return
    if selected.isdigit() and 1 <= int(selected) <= len(accounts):
        account = accounts[int(selected) - 1]
    else:
        matches = [
            account
            for account in accounts
            if selected in {account.get("account_id"), account.get("alias")}
        ]
        if len(matches) != 1:
            typer.echo(f"找不到唯一账户：{selected}")
            return
        account = matches[0]
    _enter_account_context(context, account, accounts=accounts)


def _print_account_list(
    context: InteractiveContext,
    *,
    accounts: tuple[dict[str, Any], ...] | None = None,
) -> None:
    values = accounts if accounts is not None else records(context)
    if not values:
        typer.echo("当前 workspace 没有可用账户。")
        typer.echo("可先运行 kairos account simulate 或 kairos account register。")
        return
    table = PrettyTable(
        ["序号", "account", "environment", "broker/custodian", "status", "segments"]
    )
    table.align = "l"
    for index, account in enumerate(values, start=1):
        segments = account.get("segments") or ()
        table.add_row(
            [
                index,
                account.get("account_id", "-"),
                account.get("environment", "-"),
                account.get("broker", "-"),
                account.get("status", "unknown"),
                ", ".join(str(value) for value in segments),
            ]
        )
    typer.echo(table)


def _enter_account_context(
    context: InteractiveContext,
    account: dict[str, Any],
    *,
    accounts: tuple[dict[str, Any], ...],
) -> None:
    account_id = str(account.get("account_id") or "")
    if not account_id:
        typer.echo("账户缺少 account id，无法进入。")
        return
    context.selected_account = account_id
    context.selected_launch = None
    context.shell_path = ("account", account_id)
    print_summary(context, accounts=accounts)


def _selected_account_record(context: InteractiveContext) -> dict[str, Any]:
    for account in records(context):
        if account.get("account_id") == context.selected_account:
            return account
    return {}


def print_summary(
    context: InteractiveContext,
    *,
    accounts: tuple[dict[str, Any], ...] | None = None,
) -> None:
    values = accounts if accounts is not None else records(context)
    account = next(
        (
            value
            for value in values
            if value.get("account_id") == context.selected_account
        ),
        {},
    )
    if not account:
        typer.echo("当前账户已不存在，请重新选择。")
        return
    table = PrettyTable(["账户上下文", "值"])
    table.align = "l"
    table.add_row(["account", account.get("account_id", "-")])
    table.add_row(["broker/custodian", account.get("broker", "-")])
    table.add_row(["exchange", account.get("exchange") or "-"])
    table.add_row(["environment", account.get("environment", "-")])
    table.add_row(["account model", account.get("account_model") or "unknown"])
    table.add_row(["status", account.get("status", "unknown")])
    table.add_row(
        ["segments", ", ".join(str(value) for value in account.get("segments") or ())]
    )
    table.add_row(["capabilities", ", ".join(sorted(_account_capabilities(account)))])
    typer.echo(table)
