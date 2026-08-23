"""Interactive product home and top-level navigation."""

from __future__ import annotations

import typer

from ...models import GuidedCommand, InteractiveContext, ShellAction, ShellControl


_DIRECT_ROUTES = {
    "1": "market",
    "market": "market",
    "quotes": "market",
    "launch": "launch",
    "2": "reference",
    "target": "reference",
    "targets": "reference",
    "reference": "reference",
    "data": "data",
    "system": "system",
    "project": "project",
    "research": "research",
    "risk": "risk",
    "capital": "capital",
    "notifications": "notifications",
    "config": "config",
}

_GROUP_ROUTES = {
    "3": "strategy",
    "strategy": "strategy",
    "4": "trade",
    "trade": "trade",
    "5": "data-research",
    "data-research": "data-research",
    "6": "operations",
    "operations": "operations",
}

HOME_GROUPS = frozenset((*_GROUP_ROUTES.values(),))


def is_group_path(path: tuple[str, ...]) -> bool:
    return len(path) == 1 and path[0] in HOME_GROUPS


def print_menu(context: InteractiveContext) -> None:
    if context.shell_path == ("trade",):
        typer.echo("交易管理：\n  1. 账户与订单\n  2. 风险管理\n  3. 资金管理")
        return
    if context.shell_path == ("strategy",):
        typer.echo(
            "策略运行：\n"
            "  1. 运行列表与控制\n"
            "  2. 打开运行观测台\n"
            "  3. 输出一次运行快照\n"
            "  4. 推荐诊断动作"
        )
        return
    if context.shell_path == ("data-research",):
        typer.echo("数据与研究：\n  1. 数据\n  2. 研究")
        return
    if context.shell_path == ("operations",):
        typer.echo(
            "系统与配置：\n"
            "  1. 项目工作区\n"
            "  2. 系统服务\n"
            "  3. 通知\n"
            "  4. 高级配置\n"
            "  5. 系统诊断"
        )
        return
    typer.echo(
        "\n".join(
            (
                "产品入口：",
                "  1. 市场行情",
                "  2. 市场目录",
                "  3. 策略运行",
                "  4. 交易管理",
                "  5. 数据与研究",
                "  6. 系统与配置",
                "  ?. 帮助（随时可用）",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    if context.shell_path == ("trade",):
        typer.echo("输入序号选择；可用命令：accounts/risk/capital/back/home。")
        return
    if context.shell_path == ("strategy",):
        typer.echo("输入序号选择；可用命令：launch/observe/once/doctor/back/home。")
        return
    if context.shell_path in {
        ("data-research",),
        ("operations",),
    }:
        typer.echo("输入序号选择；back 返回产品入口，home 返回首页。")
        return
    typer.echo(
        "输入 1-6 选择产品入口，输入 ? 或 help 查看帮助。也可直接输入命令："
        "account/launch/reference/"
        "market/data/research/risk/capital/system/notifications/config；"
        "market 是直连 provider 的独立模式；system/market 和 "
        "launch/<id>/instances/<instance-id>/components/market 是连接模式；"
        "输入 map 查看完整命令地图。"
    )


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if context.shell_path == ("trade",):
        if parts in {("1",), ("account",), ("accounts",), ("select",)}:
            context.shell_path = ("trade", "accounts")
            return ShellControl.HANDLED
        if parts in {("2",), ("risk",)}:
            context.shell_path = ("risk",)
            return ShellControl.HANDLED
        if parts in {("3",), ("capital",)}:
            context.shell_path = ("capital",)
            return ShellControl.HANDLED
        return None
    if context.shell_path == ("strategy",):
        from ..strategy import observe

        if parts in {("1",), ("launch",), ("runs",)}:
            context.shell_path = ("launch",)
            return ShellControl.HANDLED
        if parts in {("2",), ("observe",), ("open",)}:
            return observe.handle(context, ("open",))
        if parts in {("3",), ("once",)}:
            return observe.handle(context, ("once",))
        if parts in {("4",), ("doctor",), ("diagnose",)}:
            return observe.diagnose(context)
        return None
    if context.shell_path == ("data-research",):
        return _navigate_group(
            context,
            parts,
            {"1": "data", "data": "data", "2": "research", "research": "research"},
        )
    if context.shell_path == ("operations",):
        if parts in {("5",), ("doctor",), ("diagnose",)}:
            return GuidedCommand(("system", "doctor"), "诊断 socket、健康文件和锁")
        return _navigate_group(
            context,
            parts,
            {
                "1": "project",
                "project": "project",
                "workspace": "project",
                "2": "system",
                "system": "system",
                "3": "notifications",
                "notifications": "notifications",
                "4": "config",
                "config": "config",
            },
        )
    if parts in {("account",), ("accounts",)}:
        context.shell_path = ("trade", "accounts")
        return ShellControl.HANDLED
    if len(parts) == 1 and parts[0] in _DIRECT_ROUTES:
        context.shell_path = (_DIRECT_ROUTES[parts[0]],)
        return ShellControl.HANDLED
    if len(parts) == 1 and parts[0] in _GROUP_ROUTES:
        context.shell_path = (_GROUP_ROUTES[parts[0]],)
        return ShellControl.HANDLED
    if parts == ("system", "reference"):
        context.shell_path = ("system", "reference")
        context.selected_service = "reference"
        return ShellControl.HANDLED
    if parts == ("system", "market"):
        context.shell_path = ("system", "market")
        context.selected_service = "market"
        return ShellControl.HANDLED
    if parts in {("doctor",), ("observe",)}:
        context.shell_path = ("observe",)
        return ShellControl.HANDLED
    if parts in {("quickstart",), ("map",)}:
        return GuidedCommand(
            ("quickstart",), "查看 CLI 场景地图", needs_workspace=False
        )
    return None


def _navigate_group(
    context: InteractiveContext,
    parts: tuple[str, ...],
    routes: dict[str, str],
) -> ShellAction:
    if len(parts) != 1 or parts[0] not in routes:
        return None
    context.shell_path = (routes[parts[0]],)
    return ShellControl.HANDLED


def choose(context: InteractiveContext) -> GuidedCommand:
    from ..business import account, market, notifications, order, reference
    from ..research_data import data, research
    from ..strategy import launch, observe
    from ..system import runtime
    from . import project

    typer.echo("你想做什么？")
    choices = (
        ("1", "从零开始创建项目并运行示例"),
        ("2", "运行或查看某个策略"),
        ("3", "维护系统服务"),
        ("4", "查询账户、行情、订单或市场目录"),
        ("5", "处理数据与研究流程"),
        ("6", "诊断现在哪里不对"),
        ("7", "打开观测台"),
        ("8", "查看命令地图"),
    )
    for key, label in choices:
        typer.echo(f"  {key}. {label}")
    choice = typer.prompt("请输入序号", default="1").strip()
    if choice == "1":
        return project.choose()
    if choice == "2":
        return launch.choose(context)
    if choice == "3":
        return runtime.choose(context)
    if choice == "4":
        typer.echo(
            "  1. 账户列表\n  2. 账户余额\n  3. 账户持仓\n"
            "  4. 行情快照\n  5. 订单状态\n  6. 市场目录\n"
            "  7. 期权链\n  8. 通知配置校验"
        )
        action = typer.prompt("请输入序号", default="1").strip()
        if action in {"1", "2", "3"}:
            return account.choose(action)
        if action == "4":
            return market.choose(context)
        if action == "5":
            return order.choose()
        if action == "6":
            return reference.choose(context)
        if action == "7":
            return reference.choose_option_chain()
        if action == "8":
            return notifications.choose()
        raise typer.BadParameter("未知查询动作")
    if choice == "5":
        typer.echo(
            "  1. 列出 datasets\n  2. 审阅 data requirements\n"
            "  3. 执行 data requirements\n  4. 列出 dataset set aliases\n"
            "  5. 锁定 research plan\n  6. 发布 research gate"
        )
        action = typer.prompt("请输入序号", default="1").strip()
        if action in {"1", "2", "3", "4"}:
            data_action = {"1": "1", "2": "3", "3": "4", "4": "6"}[action]
            command = data.handle(context, (data_action,))
        else:
            research_action = "1" if action == "5" else "3"
            command = research.handle(context, (research_action,))
        if command is None:
            raise typer.BadParameter("未知 Data/Research 动作")
        return command
    if choice == "6":
        return observe.diagnose(context)
    if choice == "7":
        return observe.choose()
    return GuidedCommand(("quickstart",), "查看 CLI 场景地图", needs_workspace=False)
