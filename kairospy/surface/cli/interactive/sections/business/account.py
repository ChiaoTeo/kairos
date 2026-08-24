"""Interactive Account section."""

from __future__ import annotations

from typing import Any

from prettytable import PrettyTable
import typer

from kairospy.application.account import AccountConfigurationApplication
from kairospy.application.config import ConfigurationReferenceApplication

from ...models import (
    CommandExecution,
    GuidedCommand,
    InteractiveContext,
    ShellAction,
    ShellControl,
)


ACCOUNT_LIST_PATH = ("trade", "accounts")
RESOURCE_ACCOUNT_LIST_PATH = ("resources", "accounts")
ACCOUNT_LIST_PATHS = {ACCOUNT_LIST_PATH, RESOURCE_ACCOUNT_LIST_PATH}


def _list_path(context: InteractiveContext) -> tuple[str, str]:
    return (
        RESOURCE_ACCOUNT_LIST_PATH
        if context.shell_path[:2] == RESOURCE_ACCOUNT_LIST_PATH
        else ACCOUNT_LIST_PATH
    )


_VIEW_ACTIONS = {
    "overview": ("overview", "查询账户概览"),
    "assets": ("assets", "查询账户资产与余额"),
    "positions": ("positions", "查询账户持仓"),
    "earn": ("earn-holdings", "查询理财与质押持有"),
}


def print_menu(context: InteractiveContext) -> None:
    if context.shell_path in ACCOUNT_LIST_PATHS:
        typer.echo("交易账户：")
        _print_account_list(context)
        if context.shell_path == RESOURCE_ACCOUNT_LIST_PATH:
            typer.echo(f"  {len(records(context)) + 1}. 添加交易账户")
        else:
            typer.echo("n. 添加 paper/live 账户；输入序号选择；refresh 刷新列表。")
        return
    account_id = context.selected_account or context.shell_path[2]
    if len(context.shell_path) == 4:
        view = context.shell_path[3]
        labels = {
            "overview": "账户概览",
            "assets": "资产与余额",
            "positions": "交易仓位",
            "earn": "理财与质押",
            "fees": "费率与账户等级",
            "transfer": "资金划转",
            "settings": "配置与凭据",
        }
        typer.echo(f"{labels.get(view, view)} · {account_id}\n  r. 刷新/重新打开")
        return
    account = _selected_account_record(context)
    if context.shell_path[:2] == RESOURCE_ACCOUNT_LIST_PATH:
        _print_resource_account_detail(context, account_id, account)
        return
    provider = (
        account.get("integration_provider")
        or account.get("exchange")
        or account.get("broker")
        or "-"
    )
    environment = account.get("environment") or "-"
    status = str(account.get("status") or "unknown")
    verification_status = str(account.get("verification_status") or "pending")
    availability = (
        "可用"
        if verification_status == "verified"
        else "需重新测试"
        if verification_status == "retest_required"
        else "不可用"
        if verification_status == "failed"
        else _connection_availability(status)
    )
    products = ", ".join(str(value) for value in account.get("products") or ()) or "-"
    segments = ", ".join(str(value) for value in account.get("segments") or ()) or "-"
    alias = str(account.get("alias") or "-")
    references = (
        ConfigurationReferenceApplication(context.owner).account_references(account_id)
        if context.owner is not None
        else []
    )
    typer.echo(
        "\n".join(
            (
                f"当前账户：{account_id} · 名称：{alias}",
                f"Provider：{provider} · 产品：{products} · 分区：{segments} · 环境：{environment}",
                f"连接可用性：{availability}（账户状态：{status}）",
                f"手动验证：{_verification_label(verification_status)}",
                f"最近测试：{account.get('last_tested_at') or '-'}",
                f"已测试：{', '.join(str(item) for item in account.get('tested') or ()) or '-'}",
                f"未测试：{', '.join(str(item) for item in account.get('not_tested') or ()) or '-'}",
                f"当前配置版本：{_hash_label(account.get('current_configuration_hash'))} · 测试版本：{_hash_label(account.get('tested_configuration_hash'))}",
                f"Launch 引用：{_reference_label(references)}",
                "  1. 账户概览",
                "  2. 资产与余额",
                "  3. 交易仓位",
                "  4. 订单管理",
                "  5. 理财与质押",
                "  6. 费率与账户等级",
                "  7. 资金划转",
                "  8. 配置与凭据",
                "  t. 手动测试连接与权限（不提交订单）",
                "  e. 启用账户（配置变化后需重新测试）",
                "  d. 停用账户",
                "  x. 删除账户（有 Launch 引用时拒绝）",
                "  s. 切换账户",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    if context.shell_path in ACCOUNT_LIST_PATHS:
        typer.echo("可用命令：<序号>/select/list/back/home/exit")
        return
    if len(context.shell_path) == 4:
        typer.echo("可用命令：refresh/back/home/exit")
        return
    typer.echo(
        "可用命令：summary/assets/positions/orders/earn/fees/transfer/"
        "settings/test/enable/disable/delete/switch"
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


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if len(parts) != 1:
        return None
    key = parts[0]
    if context.shell_path in ACCOUNT_LIST_PATHS:
        accounts = records(context)
        if key in {"n", "new", "setup"} or (
            context.shell_path == RESOURCE_ACCOUNT_LIST_PATH
            and key == str(len(accounts) + 1)
        ):
            return GuidedCommand(
                ("account", "setup"),
                "配置 Workspace 交易账户并可选择执行安全的手动读取测试",
                execution=CommandExecution.INTERACTIVE,
                show_command=False,
            )
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

    account_id = context.shell_path[2]
    context.selected_account = account_id
    if len(context.shell_path) == 4:
        view = context.shell_path[3]
        if key not in {"r", "refresh", "open"}:
            return None
        if view in _VIEW_ACTIONS:
            command, summary = _VIEW_ACTIONS[view]
            return account_fact_command(context, command, summary)
        if view == "fees":
            return _fees_command(account_id)
        if view == "transfer":
            return _transfer_action(context)
        if view == "settings":
            return _account_settings_command(context)
        return None
    if context.shell_path[:2] == RESOURCE_ACCOUNT_LIST_PATH:
        account = _selected_account_record(context)
        if key in {"1", "t", "test", "verify"}:
            return GuidedCommand(
                ("account", "test", account_id, "--format", "text"),
                "验证账户认证、读取与权限；不会提交订单或划转资金",
                dangerous=True,
            )
        if key in {"2", "edit", "settings"}:
            return _account_settings_command(context)
        if key in {"3", "advanced", "status"}:
            _print_account_advanced(context, account_id, account)
            return ShellControl.HANDLED
        if key in {"4", "enable", "disable"}:
            disabled = str(account.get("status") or "").lower() == "disabled"
            enabling = key == "enable" or (key == "4" and disabled)
            return GuidedCommand(
                (
                    "account",
                    "modify",
                    "--account-id",
                    account_id,
                    "--status",
                    "configured" if enabling else "disabled",
                    "--format",
                    "text",
                ),
                f"{'启用' if enabling else '停用'}交易账户 {account_id}",
                dangerous=not enabling,
            )
        if key in {"5", "delete", "remove"}:
            return GuidedCommand(
                (
                    "account",
                    "remove",
                    "--account-id",
                    account_id,
                    "--format",
                    "text",
                ),
                "删除交易账户；存在运行方案引用时会拒绝",
                dangerous=True,
            )
        return None
    if key in {"1", "summary", "overview"}:
        return _enter_fact_view(context, "overview")
    if key in {"2", "assets", "balances"}:
        return _enter_fact_view(context, "assets")
    if key in {"3", "positions"}:
        return _enter_fact_view(context, "positions")
    if key in {"4", "orders"}:
        if not _select_order_segment(context):
            return ShellControl.HANDLED
        context.selected_order = None
        context.selected_order_symbol = None
        context.selected_market = None
        context.selected_market_provider = None
        context.shell_path = (*_list_path(context), account_id, "orders")
        return ShellControl.HANDLED
    if key in {"5", "earn", "earn-holdings"}:
        return _enter_fact_view(context, "earn")
    if key in {"6", "fees"}:
        context.shell_path = (*_list_path(context), account_id, "fees")
        return _fees_command(account_id)
    if key in {"7", "transfer"}:
        context.shell_path = (*_list_path(context), account_id, "transfer")
        return _transfer_action(context)
    if key in {"8", "settings", "configuration"}:
        context.shell_path = (*_list_path(context), account_id, "settings")
        return _account_settings_command(context)
    if key in {"t", "test", "verify"}:
        return GuidedCommand(
            ("account", "test", account_id, "--format", "text"),
            "手动验证账户认证、读取与权限；不会提交订单或划转资金",
            dangerous=True,
        )
    if key in {"e", "enable"}:
        return GuidedCommand(
            (
                "account",
                "modify",
                "--account-id",
                account_id,
                "--status",
                "configured",
                "--format",
                "text",
            ),
            "启用账户；配置版本变化后必须重新执行手动测试",
        )
    if key in {"d", "disable"}:
        return GuidedCommand(
            (
                "account",
                "modify",
                "--account-id",
                account_id,
                "--status",
                "disabled",
                "--format",
                "text",
            ),
            "停用 Workspace 交易账户",
            dangerous=True,
        )
    if key in {"x", "delete", "remove"}:
        return GuidedCommand(
            (
                "account",
                "remove",
                "--account-id",
                account_id,
                "--format",
                "text",
            ),
            "删除 Workspace 交易账户；存在 Launch 引用时会拒绝",
            dangerous=True,
        )
    if key in {"s", "switch", "select"}:
        context.selected_account = None
        context.selected_account_provider = None
        context.selected_account_environment = None
        context.selected_account_segment = None
        context.selected_order = None
        context.selected_order_symbol = None
        context.selected_market = None
        context.selected_market_provider = None
        context.shell_path = _list_path(context)
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


def build_fact_command(account_id: str, command: str, summary: str) -> GuidedCommand:
    return GuidedCommand(("account", command, account_id, "--format", "table"), summary)


def _enter_fact_view(context: InteractiveContext, view: str) -> GuidedCommand:
    account_id = context.selected_account or ""
    context.shell_path = (*_list_path(context), account_id, view)
    command, summary = _VIEW_ACTIONS[view]
    result = account_fact_command(context, command, summary)
    assert isinstance(result, GuidedCommand)
    return result


def _fees_command(account_id: str) -> GuidedCommand:
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


def _transfer_action(context: InteractiveContext) -> ShellAction:
    account = _selected_account_record(context)
    if "transfer" not in _account_capabilities(account):
        typer.echo("当前账户凭据不具备资金划转能力。")
        return ShellControl.HANDLED
    typer.echo("资金划转必须先 preview，再由用户确认执行；当前尚未开放执行。")
    return ShellControl.HANDLED


def _account_settings_command(context: InteractiveContext) -> GuidedCommand:
    account_id = context.selected_account or ""
    action = _prompt_menu(
        "账户配置与凭据：",
        (
            ("1", "查看账户配置"),
            ("2", "修改名称、环境与 segment"),
            ("3", "运行账户诊断"),
            ("4", "查看凭据列表"),
        ),
    )
    if action == "2":
        account = _selected_account_record(context)
        alias = typer.prompt(
            "账户名称", default=str(account.get("alias") or account_id)
        ).strip()
        environment = typer.prompt(
            "环境", default=str(account.get("environment") or "paper")
        ).strip()
        current_segments = account.get("segments") or ("spot",)
        segment = typer.prompt(
            "segment", default=str(next(iter(current_segments), "spot"))
        ).strip()
        return GuidedCommand(
            (
                "account",
                "modify",
                "--account-id",
                account_id,
                "--alias",
                alias,
                "--environment",
                environment,
                "--segment",
                segment,
                "--format",
                "text",
            ),
            "修改账户基础配置；保存后需重新执行手动测试",
        )
    mapping = {
        "1": (("account", "show", "--account-id", account_id), "查看账户配置"),
        "3": (
            ("account", "doctor", "--account-id", account_id),
            "运行账户诊断",
        ),
        "4": (("account", "credential-list"), "查看凭据列表"),
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


def _connection_availability(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"connected", "ready", "simulated"}:
        return "可用"
    if normalized == "configured":
        return "未探测"
    return "不可用"


def _verification_label(status: str) -> str:
    return {
        "verified": "可用",
        "pending": "需要测试",
        "retest_required": "配置已变化",
        "failed": "连接失败",
        "disabled": "已禁用",
    }.get(status, status)


def _print_resource_account_detail(
    context: InteractiveContext, account_id: str, account: dict[str, Any]
) -> None:
    provider = (
        account.get("integration_provider")
        or account.get("exchange")
        or account.get("broker")
        or "本地模拟"
    )
    status = str(account.get("verification_status") or "pending")
    disabled = str(account.get("status") or "").lower() == "disabled"
    typer.echo(
        f"交易账户：{account.get('alias') or account_id}\n\n"
        f"状态：{_verification_label('disabled' if disabled else status)}\n"
        f"类型：{_account_risk_label(account)}\n"
        f"服务商：{provider}\n"
        f"最近测试：{account.get('last_tested_at') or '尚未测试'}\n\n"
        "建议操作：\n"
        "  1. 测试连接\n"
        "  2. 修改配置\n"
        "  3. 安全与高级信息\n"
        + ("  4. 启用\n" if disabled else "  4. 停用\n")
        + "  5. 删除"
    )


def _print_account_advanced(
    context: InteractiveContext, account_id: str, account: dict[str, Any]
) -> None:
    references = (
        ConfigurationReferenceApplication(context.owner).account_references(account_id)
        if context.owner is not None
        else []
    )
    typer.echo(
        "安全与高级信息：\n"
        f"  Account ID：{account_id}\n"
        f"  认证资料：{account.get('credential_id') or '-'}（值不显示）\n"
        f"  配置版本：{_hash_label(account.get('current_configuration_hash'))}\n"
        f"  测试版本：{_hash_label(account.get('tested_configuration_hash'))}\n"
        f"  已测试：{', '.join(str(item) for item in account.get('tested') or ()) or '-'}\n"
        f"  未测试：{', '.join(str(item) for item in account.get('not_tested') or ()) or '-'}\n"
        f"  运行方案引用：{_reference_label(references)}"
    )


def _account_risk_label(account: dict[str, Any]) -> str:
    environment = str(account.get("environment") or "unknown").lower()
    environment_label = (
        "实盘"
        if environment == "live"
        else "模拟"
        if environment in {"paper", "simulated"}
        else environment
    )
    capabilities = _account_capabilities(account)
    permission_label = (
        "可划转"
        if "transfer" in capabilities
        else "可交易"
        if "trade" in capabilities
        else "只读"
    )
    return f"{environment_label} · {permission_label}"


def _reference_label(references: list[dict[str, str]]) -> str:
    if not references:
        return "无"
    return "；".join(f"{item['source']}:{item['location']}" for item in references)


def _hash_label(value: object) -> str:
    return str(value)[:12] if isinstance(value, str) and value else "-"


def records(context: InteractiveContext) -> tuple[dict[str, Any], ...]:
    if context.owner is None:
        return ()
    try:
        return tuple(AccountConfigurationApplication(context.owner).list())
    except (OSError, RuntimeError, ValueError) as error:
        typer.echo(f"读取账户列表失败：{error}")
        return ()


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
    table = PrettyTable(["序号", "账户", "类型/权限", "服务商", "验证", "市场范围"])
    table.align = "l"
    for index, account in enumerate(values, start=1):
        segments = account.get("segments") or ()
        table.add_row(
            [
                index,
                account.get("account_id", "-"),
                _account_risk_label(account),
                account.get("broker", "-"),
                _verification_label(
                    str(account.get("verification_status") or "pending")
                ),
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
    context.selected_account_provider = str(
        account.get("integration_provider")
        or account.get("exchange")
        or account.get("broker")
        or "unknown"
    )
    context.selected_account_environment = str(account.get("environment") or "unknown")
    segments = [str(value) for value in account.get("segments") or ()]
    context.selected_account_segment = segments[0] if len(segments) == 1 else None
    context.selected_launch = None
    context.selected_launch_mode = None
    context.selected_launch_instance = None
    context.selected_order = None
    context.selected_order_symbol = None
    context.selected_market = None
    context.selected_market_provider = None
    context.shell_path = (*_list_path(context), account_id)
    print_summary(context, accounts=accounts)


def _select_order_segment(context: InteractiveContext) -> bool:
    if context.selected_account_segment:
        return True
    account = _selected_account_record(context)
    segments = [str(value) for value in account.get("segments") or ()]
    if not segments:
        typer.echo("当前账户没有可用交易分区。")
        return False
    if len(segments) == 1:
        context.selected_account_segment = segments[0]
        return True
    typer.echo("选择订单操作分区：")
    for index, segment in enumerate(segments, start=1):
        typer.echo(f"  {index}. {segment}")
    selected = typer.prompt("选择分区序号", default="1").strip()
    if not selected.isdigit() or not 1 <= int(selected) <= len(segments):
        typer.echo(f"找不到分区序号：{selected}")
        return False
    context.selected_account_segment = segments[int(selected) - 1]
    return True


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
        [
            "verification",
            _verification_label(str(account.get("verification_status") or "pending")),
        ]
    )
    table.add_row(["last tested", account.get("last_tested_at") or "-"])
    table.add_row(
        [
            "tested",
            ", ".join(str(value) for value in account.get("tested") or ()) or "-",
        ]
    )
    table.add_row(
        [
            "not tested",
            ", ".join(str(value) for value in account.get("not_tested") or ()) or "-",
        ]
    )
    table.add_row(
        ["segments", ", ".join(str(value) for value in account.get("segments") or ())]
    )
    table.add_row(["capabilities", ", ".join(sorted(_account_capabilities(account)))])
    typer.echo(table)
