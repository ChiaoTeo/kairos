from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import shlex

from prettytable import PrettyTable
import typer

from kairospy.application.launch.application import LaunchRegistryApplication
from kairospy.application.system import ComponentProcessApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.console.data import SystemObserveReader
from kairospy.surface.console.models import ObserveSnapshot, recommended_action


ExecuteCommand = Callable[[Sequence[str]], int]


class ShellControl(Enum):
    HANDLED = "handled"


@dataclass(frozen=True, slots=True)
class GuidedCommand:
    argv: tuple[str, ...]
    summary: str
    dangerous: bool = False
    needs_workspace: bool = True


ShellAction = GuidedCommand | ShellControl | None


@dataclass(slots=True)
class InteractiveContext:
    owner: object | None
    snapshot: ObserveSnapshot | None
    workspace_arg: Path | None
    selected_launch: str | None = None
    selected_service: str | None = None
    reference_asset_code: str | None = None
    last_command: str | None = None
    last_status: int | None = None
    shell_path: tuple[str, ...] = ()


def run_interactive(
    *,
    workspace: Path | None,
    dry_run: bool,
    no_exec: bool,
    yes: bool,
    execute: ExecuteCommand,
) -> int:
    """Run the interactive Kairos operator shell."""

    typer.echo("Kairos 交互式操作")
    typer.echo("选择你想完成的事情，Kairos 会引导你完成下一步。")
    typer.echo()

    owner = _workspace(workspace)
    context = InteractiveContext(
        owner=owner,
        snapshot=_snapshot(owner) if owner is not None else None,
        workspace_arg=workspace,
    )
    if dry_run or no_exec:
        return _run_one_shot_preview(context)
    return _run_shell(context, execute=execute, yes=yes)


def _run_one_shot_preview(context: InteractiveContext) -> int:
    _print_context(context)
    command = _choose_command(context)
    argv = _with_workspace(command, context.workspace_arg)
    typer.echo()
    typer.echo(f"准备执行：{_display_command(argv)}")
    typer.echo(f"用途：{command.summary}")
    typer.echo("已开启 dry-run/no-exec，只展示命令，不执行。")
    return 0


def _run_shell(
    context: InteractiveContext, *, execute: ExecuteCommand, yes: bool
) -> int:
    _print_context(context)
    typer.echo("输入序号选择产品动作；也可以输入命令。exit 退出。")
    while True:
        _print_shell_menu(context)
        try:
            line = input(f"{_prompt_path(context)}> ").strip()
        except EOFError:
            typer.echo()
            return context.last_status or 0
        if not line:
            continue
        if line in {"exit", "quit", "q"}:
            return context.last_status or 0
        if line == "help":
            _print_shell_help(context)
            continue
        if line == "summary":
            _print_context(context)
            continue
        if line == "status" and not context.shell_path:
            _print_context(context)
            continue
        if line == "refresh":
            _refresh_context(context)
            _print_context(context)
            continue
        if line in {"home", "/"}:
            context.shell_path = ()
            continue
        if line == "back":
            context.shell_path = context.shell_path[:-1]
            if context.shell_path != ("system",):
                context.selected_service = None
            continue
        command = _shell_command(context, line)
        if command is ShellControl.HANDLED:
            continue
        if command is None:
            typer.echo("无法识别这个命令。输入 help 查看当前上下文可用动作。")
            continue
        _execute_guided_command(context, command, execute=execute, yes=yes)


def _prompt_path(context: InteractiveContext) -> str:
    return "/" + "/".join(context.shell_path)


def _print_shell_menu(context: InteractiveContext) -> None:
    path = context.shell_path
    if not path:
        typer.echo(
            "\n".join(
                (
                    "产品入口：",
                    "  1. 系统服务",
                    "  2. 策略运行",
                    "  3. Reference 查询",
                    "  4. 账户 / 行情 / 订单",
                    "  5. 数据与研究",
                    "  6. 诊断",
                    "  7. 观测台",
                    "  8. 命令地图",
                )
            )
        )
        return
    if path == ("launch",):
        typer.echo(
            "\n".join(
                (
                    "策略运行：",
                    "  1. 选择 launch",
                    "  2. 启动",
                    "  3. 查看状态",
                    "  4. 查看日志",
                    "  5. 跟随状态和输出",
                    "  6. 等待回测报告",
                    "  7. 停止",
                    "  8. 校验配置",
                    "  9. 编辑配置",
                    "  10. 查看最近报告",
                )
            )
        )
        return
    if path == ("system",):
        typer.echo(
            "\n".join(
                (
                    "系统服务：",
                    "  1. reference",
                    "  2. market",
                    "  3. 查看所有系统服务",
                    "  4. 运行 system doctor",
                    "  5. 修复 stale 运行资源",
                )
            )
        )
        return
    if path == ("reference",):
        typer.echo(
            "\n".join(
                (
                    "Reference 查询：",
                    "  1. 当前系统有哪些 market",
                    "  2. 某个 asset/symbol 相关的 market",
                    "  3. 有哪些 listing",
                    "  4. 某个 market 的信息",
                    "  5. 按 symbol 检索",
                    "  6. 期权链",
                )
            )
        )
        return
    if path == ("query",):
        typer.echo(
            "\n".join(
                (
                    "账户 / 行情 / 订单：",
                    "  1. 账户列表",
                    "  2. 账户余额",
                    "  3. 账户持仓",
                    "  4. 行情快照",
                    "  5. 订单状态",
                    "  6. 通知配置校验",
                    "  7. Provider 集成帮助",
                )
            )
        )
        return
    if path == ("data",):
        typer.echo(
            "\n".join(
                (
                    "数据与研究：",
                    "  1. 列出 datasets",
                    "  2. 审阅 data requirements",
                    "  3. 执行 data requirements",
                    "  4. 列出 dataset set aliases",
                    "  5. 锁定 research plan",
                    "  6. 发布 research gate",
                )
            )
        )
        return
    if path in {("system", "reference"), ("system", "market")}:
        service = path[-1]
        typer.echo(
            "\n".join(
                (
                    f"{service} 动作：",
                    "  1. 查看状态",
                    "  2. 启动",
                    "  3. 停止",
                    "  4. 重启",
                    "  5. 查看日志",
                )
            )
        )


def _print_shell_help(context: InteractiveContext) -> None:
    path = context.shell_path
    if not path:
        typer.echo(
            "\n".join(
                (
                    "可用命令：",
                    "  system              进入系统服务",
                    "  launch              进入策略运行",
                    "  reference           进入 Reference 查询",
                    "  query               进入账户 / 行情 / 订单",
                    "  data                进入数据与研究",
                    "  system reference    进入 /system/reference",
                    "  system market       进入 /system/market",
                    "  summary             显示当前概览",
                    "  refresh             刷新状态",
                    "  exit                退出",
                )
            )
        )
        return
    if path == ("launch",):
        typer.echo(
            "\n".join(
                (
                    "可用命令：",
                    "  select              选择 launch",
                    "  start/status/logs/attach/wait/stop/validate/edit/report",
                    "  back                返回上一级",
                    "  home                回到根上下文",
                )
            )
        )
        return
    if path == ("system",):
        typer.echo(
            "\n".join(
                (
                    "可用命令：",
                    "  reference           进入 reference 服务",
                    "  market              进入 market 服务",
                    "  list                查看系统服务列表",
                    "  doctor              运行 system doctor",
                    "  repair              修复 stale 运行资源",
                    "  back                返回上一级",
                    "  home                回到根上下文",
                )
            )
        )
        return
    if path == ("reference",):
        typer.echo(
            "\n".join(
                (
                    "可用命令：",
                    "  markets             查看当前可用 market",
                    "  asset               查看某个 asset/symbol 相关 market",
                    "  listings            查看 listing",
                    "  market              查看某个 market 信息",
                    "  search              按 symbol 检索",
                    "  option-chain        查询期权链",
                    "  back/home/exit",
                )
            )
        )
        return
    if path == ("query",):
        typer.echo(
            "可用命令：accounts/balances/positions/snapshot/order/notifications/integration"
        )
        return
    if path == ("data",):
        typer.echo("可用命令：list/plan/execute/sets/lock/gate")
        return
    if path in {("system", "reference"), ("system", "market")}:
        service = path[-1]
        typer.echo(
            "\n".join(
                (
                    f"当前服务：{service}",
                    "可用命令：",
                    "  status              查看状态",
                    "  start               启动服务",
                    "  stop                停止服务",
                    "  restart             重启服务",
                    "  logs                查看日志",
                    "  back                返回 /system",
                    "  home                回到根上下文",
                )
            )
        )
        return
    typer.echo("输入 back 返回上一级，home 回到根上下文，exit 退出。")


def _refresh_context(context: InteractiveContext) -> None:
    context.owner = _workspace(context.workspace_arg)
    context.snapshot = _snapshot(context.owner) if context.owner is not None else None


def _shell_command(context: InteractiveContext, line: str) -> ShellAction:
    try:
        parts = tuple(shlex.split(line))
    except ValueError as error:
        typer.echo(f"命令解析失败：{error}")
        return None
    if not parts:
        return None
    path = context.shell_path
    if not path:
        return _root_shell_command(context, parts)
    if path == ("launch",):
        return _launch_shell_command(context, parts)
    if path == ("system",):
        return _system_shell_command(context, parts)
    if path == ("reference",):
        return _reference_shell_command(context, parts)
    if path == ("query",):
        return _query_shell_command(context, parts)
    if path == ("data",):
        return _data_shell_command(parts)
    if path in {("system", "reference"), ("system", "market")}:
        return _system_service_shell_command(context, parts)
    return None


def _root_shell_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if parts in {("1",), ("system",)}:
        context.shell_path = ("system",)
        return ShellControl.HANDLED
    if parts in {("2",), ("launch",)}:
        context.shell_path = ("launch",)
        return ShellControl.HANDLED
    if parts in {("3",), ("reference",)}:
        context.shell_path = ("reference",)
        return ShellControl.HANDLED
    if parts in {("4",), ("query",), ("account",), ("market-data",)}:
        context.shell_path = ("query",)
        return ShellControl.HANDLED
    if parts in {("5",), ("data",), ("research",)}:
        context.shell_path = ("data",)
        return ShellControl.HANDLED
    if parts in {("system", "reference"), ("reference",)}:
        context.shell_path = ("system", "reference")
        context.selected_service = "reference"
        return ShellControl.HANDLED
    if parts in {("system", "market"), ("market",)}:
        context.shell_path = ("system", "market")
        context.selected_service = "market"
        return ShellControl.HANDLED
    if parts in {("8",), ("quickstart",), ("map",)}:
        return GuidedCommand(
            ("quickstart",), "查看 CLI 场景地图", needs_workspace=False
        )
    if parts in {("6",), ("doctor",)}:
        return GuidedCommand(("project", "doctor"), "检查项目 readiness")
    if parts in {("7",), ("observe",)}:
        return GuidedCommand(("observe",), "打开项目观测台")
    return None


def _launch_shell_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if parts in {("1",), ("select",)}:
        context.selected_launch = _prompt_launch_id(
            context.owner, context.snapshot, context.selected_launch
        )
        return ShellControl.HANDLED
    mapping = {
        "2": ("start", "启动策略运行", True),
        "start": ("start", "启动策略运行", True),
        "3": ("status", "查看策略和依赖状态", False),
        "status": ("status", "查看策略和依赖状态", False),
        "4": ("logs", "查看策略日志", False),
        "logs": ("logs", "查看策略日志", False),
        "5": ("attach", "跟随状态和最近输出", False),
        "attach": ("attach", "跟随状态和最近输出", False),
        "6": ("wait", "等待回测完成并读取报告", False),
        "wait": ("wait", "等待回测完成并读取报告", False),
        "7": ("stop", "停止策略并释放运行资源", True),
        "stop": ("stop", "停止策略并释放运行资源", True),
        "8": ("diagnose validate", "校验 launch 配置", False),
        "validate": ("diagnose validate", "校验 launch 配置", False),
        "9": ("edit", "交互式编辑 launch 配置", True),
        "edit": ("edit", "交互式编辑 launch 配置", True),
        "10": ("report", "读取最近完成的报告", False),
        "report": ("report", "读取最近完成的报告", False),
    }
    selected = mapping.get(parts[0]) if len(parts) == 1 else None
    if selected is None:
        return None
    action, summary, dangerous = selected
    launch_id = context.selected_launch or _prompt_launch_id(
        context.owner, context.snapshot, context.selected_launch
    )
    context.selected_launch = launch_id
    return GuidedCommand(
        ("launch", *tuple(action.split()), launch_id),
        summary,
        dangerous=dangerous,
    )


def _system_shell_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if parts in {("1",), ("reference",)}:
        context.shell_path = ("system", "reference")
        context.selected_service = "reference"
        return ShellControl.HANDLED
    if parts in {("2",), ("market",)}:
        context.shell_path = ("system", "market")
        context.selected_service = "market"
        return ShellControl.HANDLED
    if parts in {("3",), ("list",), ("ls",), ("status",)}:
        return GuidedCommand(
            ("system", "list", "--format", "table"), "列出 workspace 系统服务状态"
        )
    if parts in {("4",), ("doctor",)}:
        return GuidedCommand(("system", "doctor"), "诊断 socket、健康文件和锁")
    if parts in {("5",), ("repair",)}:
        return GuidedCommand(
            ("system", "repair"), "清理确认 stale 的运行资源", dangerous=True
        )
    return None


def _system_service_shell_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    component = context.shell_path[-1]
    context.selected_service = component
    mapping = {
        "1": (
            ("system", "status", "--component", component, "--format", "text"),
            f"查看 {component} 状态",
            False,
        ),
        "status": (
            ("system", "status", "--component", component, "--format", "text"),
            f"查看 {component} 状态",
            False,
        ),
        "2": (
            ("system", "up", "--component", component, "--format", "text"),
            f"启动 {component}",
            True,
        ),
        "start": (
            ("system", "up", "--component", component, "--format", "text"),
            f"启动 {component}",
            True,
        ),
        "3": (
            ("system", "down", "--component", component, "--format", "text"),
            f"停止 {component}",
            True,
        ),
        "stop": (
            ("system", "down", "--component", component, "--format", "text"),
            f"停止 {component}",
            True,
        ),
        "4": (
            ("system", "restart", "--component", component, "--format", "text"),
            f"重启 {component}",
            True,
        ),
        "restart": (
            ("system", "restart", "--component", component, "--format", "text"),
            f"重启 {component}",
            True,
        ),
        "5": (
            ("system", "logs", "--component", component),
            f"查看 {component} 日志",
            False,
        ),
        "logs": (
            ("system", "logs", "--component", component),
            f"查看 {component} 日志",
            False,
        ),
    }
    selected = mapping.get(parts[0]) if len(parts) == 1 else None
    if selected is None:
        return None
    argv, summary, dangerous = selected
    return GuidedCommand(argv, summary, dangerous=dangerous)


def _reference_shell_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    key = parts[0]
    if len(parts) != 1:
        return None
    if key in {"1", "markets"}:
        limit = typer.prompt("最多显示多少个 market", default="50").strip()
        return GuidedCommand(
            (
                "reference",
                "markets",
                "--active-only",
                "--limit",
                limit,
                "--format",
                "table",
            ),
            "查看当前可用 market",
        )
    if key in {"2", "asset"}:
        asset_code = typer.prompt(
            "asset code / symbol", default=context.reference_asset_code or "AAPL"
        ).strip()
        context.reference_asset_code = asset_code
        return GuidedCommand(
            (
                "reference",
                "markets",
                "--asset-code",
                asset_code,
                "--active-only",
                "--format",
                "table",
            ),
            f"查看 {asset_code} 相关的 market",
        )
    if key in {"3", "listings"}:
        symbol = typer.prompt("symbol（可留空）", default="").strip()
        argv = ("reference", "listings", "--active-only", "--format", "table")
        if symbol:
            argv = (*argv, "--symbol", symbol)
        return GuidedCommand(argv, "查看 listing")
    if key in {"4", "market"}:
        market_id = typer.prompt(
            "market id", default="market:binance:spot:BTCUSDT"
        ).strip()
        return GuidedCommand(
            ("reference", "markets", "--market-id", market_id, "--format", "text"),
            "查看指定 market 信息",
        )
    if key in {"5", "search"}:
        symbol = typer.prompt("symbol", default="BTCUSDT").strip()
        target = _prompt_menu(
            "你想在哪类对象里检索？",
            (
                ("1", "instrument"),
                ("2", "market"),
                ("3", "listing"),
            ),
        )
        if target == "1":
            return GuidedCommand(
                ("reference", "instruments", "--symbol", symbol, "--format", "table"),
                "按 symbol 检索 instrument",
            )
        if target == "2":
            return GuidedCommand(
                ("reference", "markets", "--symbol", symbol, "--format", "table"),
                "按 symbol 检索 market",
            )
        return GuidedCommand(
            ("reference", "listings", "--symbol", symbol, "--format", "table"),
            "按 symbol 检索 listing",
        )
    if key in {"6", "option-chain"}:
        underlying = typer.prompt(
            "underlying instrument id",
            default="instrument:equity:US:AAPL:common",
        ).strip()
        return GuidedCommand(
            (
                "reference",
                "option-chain",
                "--underlying-instrument-id",
                underlying,
                "--format",
                "table",
            ),
            "查询期权链",
        )
    return None


def _query_shell_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    key = parts[0]
    if len(parts) != 1:
        return None
    if key in {"1", "accounts"}:
        return GuidedCommand(("account", "list", "--output", "table"), "查看已配置账户")
    if key in {"2", "balances", "3", "positions"}:
        account = typer.prompt("账户 id", default="demo-paper").strip()
        command = "balances" if key in {"2", "balances"} else "positions"
        return GuidedCommand(
            ("account", "--account-id", account, command, "--output", "table"),
            "查看账户事实",
        )
    if key in {"4", "snapshot"}:
        kind = _prompt_component(
            "快照类型", default="quote", choices=("quote", "bar", "greeks")
        )
        market_id = typer.prompt(
            "market id", default="market:binance:spot:BTCUSDT"
        ).strip()
        source_id = typer.prompt("source id", default="binance-spot").strip()
        return GuidedCommand(
            (
                "market",
                "snapshot",
                kind,
                "--market-id",
                market_id,
                "--source-id",
                source_id,
                "--format",
                "table",
            ),
            "读取运行中 Market 快照",
        )
    if key in {"5", "order"}:
        order_id = typer.prompt("order id", default="").strip()
        if not order_id:
            raise typer.BadParameter("order id 不能为空")
        return GuidedCommand(
            ("order", "status", "--order-id", order_id, "--output", "text"),
            "查看订单状态",
        )
    if key in {"6", "notifications"}:
        return GuidedCommand(
            ("notifications", "validate", "--format", "text"), "校验通知目的地"
        )
    if key in {"7", "integration"}:
        return GuidedCommand(
            ("integration", "--help"),
            "查看 Provider 集成入口；真实调用通常需要凭据",
            needs_workspace=False,
        )
    return None


def _data_shell_command(parts: tuple[str, ...]) -> GuidedCommand | None:
    key = parts[0]
    if len(parts) != 1:
        return None
    if key in {"1", "list"}:
        return GuidedCommand(("data", "list"), "列出项目 datasets")
    if key in {"2", "plan"}:
        path = typer.prompt(
            "requirements.json 路径", default="requirements.json"
        ).strip()
        return GuidedCommand(("data", "plan", path), "审阅数据需求，不下载")
    if key in {"3", "execute"}:
        path = typer.prompt(
            "requirements.json 路径", default="requirements.json"
        ).strip()
        plan_hash = typer.prompt("expected plan hash", default="").strip()
        argv = ("data", "execute", path)
        if plan_hash:
            argv = (*argv, "--expected-plan-hash", plan_hash)
        return GuidedCommand(argv, "执行已经审阅的数据计划", dangerous=True)
    if key in {"4", "sets"}:
        return GuidedCommand(("data", "set", "list"), "列出 Dataset Set aliases")
    if key in {"5", "lock"}:
        path = typer.prompt(
            "research-plan.json 路径", default="research-plan.json"
        ).strip()
        return GuidedCommand(("research", "plan", "lock", path), "锁定研究计划")
    if key in {"6", "gate"}:
        plan = typer.prompt(
            "research-plan.json 路径", default="research-plan.json"
        ).strip()
        evidence = typer.prompt(
            "research-evidence.json 路径", default="research-evidence.json"
        ).strip()
        return GuidedCommand(
            ("research", "gate", "publish", plan, evidence),
            "发布研究 gate 证据",
            dangerous=True,
        )
    return None


def _execute_guided_command(
    context: InteractiveContext,
    command: GuidedCommand,
    *,
    execute: ExecuteCommand,
    yes: bool,
) -> None:
    argv = _with_workspace(command, context.workspace_arg)
    display = _display_command(argv)
    typer.echo(f"准备执行：{display}")
    typer.echo(f"用途：{command.summary}")
    if command.dangerous and not yes:
        typer.echo("这个动作可能改变运行状态。")
    if not yes and not typer.confirm("确认执行这个命令吗？", default=True):
        typer.echo("已取消。")
        context.last_command = display
        context.last_status = 0
        return
    context.last_command = display
    context.last_status = execute(argv)
    _refresh_context(context)


def _workspace(workspace: Path | None):
    try:
        return WorkspaceApplication().resolve(workspace)
    except (FileNotFoundError, ValueError) as error:
        typer.echo("当前没有识别到 Kairos 项目。")
        typer.echo(f"原因：{error}")
        typer.echo("你仍然可以选择“从零开始”来创建项目。")
        typer.echo()
        return None


def _snapshot(owner) -> ObserveSnapshot | None:
    try:
        return SystemObserveReader(
            ComponentProcessApplication(owner), owner.workspace_id
        ).read()
    except Exception as error:
        typer.echo(f"读取 workspace 状态失败：{error}")
        return None


def _print_context(context: InteractiveContext) -> None:
    owner = context.owner
    snapshot = context.snapshot
    if owner is None:
        return
    typer.echo(_workspace_table(owner.workspace_id, str(owner.paths.project_root)))
    typer.echo()
    typer.echo("当前上下文")
    typer.echo(_context_table(context))
    typer.echo()
    if snapshot is None:
        typer.echo("状态：暂时无法读取运行状态")
        typer.echo()
        return
    typer.echo("策略运行")
    typer.echo(_launch_table(snapshot))
    typer.echo()
    typer.echo("系统服务")
    typer.echo(_shared_service_table(snapshot))
    typer.echo(f"建议：{recommended_action(snapshot)}")
    typer.echo()


def _context_table(context: InteractiveContext) -> str:
    table = PrettyTable(["上下文", "值"])
    table.align = "l"
    table.add_row(["launch", context.selected_launch or "-"])
    table.add_row(["system service", context.selected_service or "-"])
    table.add_row(["reference asset", context.reference_asset_code or "-"])
    table.add_row(["last command", context.last_command or "-"])
    table.add_row(
        [
            "last status",
            "-" if context.last_status is None else str(context.last_status),
        ]
    )
    return str(table)


def _workspace_table(workspace_id: str, project_root: str) -> str:
    table = PrettyTable(["项目", "值"])
    table.align = "l"
    table.add_row(["workspace", workspace_id])
    table.add_row(["路径", project_root])
    return str(table)


def _launch_table(snapshot: ObserveSnapshot) -> str:
    launches = _unique_launches(snapshot)
    if not launches:
        return "暂无 launch 记录"
    table = PrettyTable(["launch", "mode", "state", "instance"])
    table.align = "l"
    for item in launches[:5]:
        table.add_row(
            [
                str(item.get("launch_id", "-")),
                str(item.get("mode", "-")),
                str(item.get("state", "unknown")),
                str(item.get("instance_id", "-")),
            ]
        )
    return str(table)


def _unique_launches(snapshot: ObserveSnapshot) -> tuple[dict[str, object], ...]:
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


def _shared_service_table(snapshot: ObserveSnapshot) -> str:
    table = PrettyTable(["service", "status"])
    table.align = "l"
    for name in ("reference", "market"):
        status = snapshot.components.get(name, {}).get("status", "unknown")
        table.add_row([name, status])
    return str(table)


def _choose_command(context: InteractiveContext) -> GuidedCommand:
    choice = _prompt_menu(
        "你想做什么？",
        (
            ("1", "从零开始创建项目并运行示例"),
            ("2", "运行或查看某个策略"),
            ("3", "维护系统服务"),
            ("4", "查询账户、行情、订单或 Reference"),
            ("5", "处理数据与研究流程"),
            ("6", "诊断现在哪里不对"),
            ("7", "打开观测台"),
            ("8", "查看命令地图"),
        ),
    )
    if choice == "1":
        return _start_from_scratch()
    if choice == "2":
        return _strategy_workflow(context)
    if choice == "3":
        return _system_workflow(context)
    if choice == "4":
        return _convenience_workflow(context)
    if choice == "5":
        return _data_research_workflow()
    if choice == "6":
        return _diagnose_workflow(context.owner, context.snapshot)
    if choice == "7":
        return GuidedCommand(("observe",), "打开项目观测台")
    return GuidedCommand(("quickstart",), "查看 CLI 场景地图", needs_workspace=False)


def _start_from_scratch() -> GuidedCommand:
    root = typer.prompt("项目目录", default="my-project").strip()
    default_id = Path(root).expanduser().name or "my-project"
    workspace_id = typer.prompt("项目名 / workspace id", default=default_id).strip()
    template = typer.prompt("模板", default="backtest").strip() or "backtest"
    return GuidedCommand(
        (
            "project",
            "init",
            root,
            "--id",
            workspace_id,
            "--template",
            template,
        ),
        "创建项目；backtest 模板会生成离线可跑的示例策略",
        dangerous=True,
        needs_workspace=False,
    )


def _strategy_workflow(context: InteractiveContext) -> GuidedCommand:
    launch_id = _prompt_launch_id(
        context.owner, context.snapshot, context.selected_launch
    )
    context.selected_launch = launch_id
    action = _prompt_menu(
        "你想对这个策略做什么？",
        (
            ("1", "启动"),
            ("2", "查看状态"),
            ("3", "查看日志"),
            ("4", "跟随状态和输出"),
            ("5", "等待回测报告"),
            ("6", "停止"),
            ("7", "校验配置"),
            ("8", "编辑配置"),
            ("9", "查看最近报告"),
        ),
    )
    mapping = {
        "1": (("launch", "start", launch_id), "启动策略运行", True),
        "2": (("launch", "status", launch_id), "查看策略和依赖状态", False),
        "3": (("launch", "logs", launch_id), "查看策略日志", False),
        "4": (("launch", "attach", launch_id), "跟随状态和最近输出", False),
        "5": (("launch", "wait", launch_id), "等待回测完成并读取报告", False),
        "6": (("launch", "stop", launch_id), "停止策略并释放运行资源", True),
        "7": (
            ("launch", "diagnose", "validate", launch_id),
            "校验 launch 配置",
            False,
        ),
        "8": (("launch", "edit", launch_id), "交互式编辑 launch 配置", True),
        "9": (("launch", "report", launch_id), "读取最近完成的报告", False),
    }
    argv, summary, dangerous = mapping[action]
    return GuidedCommand(argv, summary, dangerous=dangerous)


def _system_workflow(context: InteractiveContext) -> GuidedCommand:
    service = _prompt_menu(
        "你想维护哪个系统服务？",
        (
            ("1", "reference"),
            ("2", "market"),
            ("3", "查看所有系统服务"),
            ("4", "运行 system doctor"),
            ("5", "修复 stale 运行资源"),
        ),
    )
    if service == "3":
        return GuidedCommand(
            ("system", "list", "--format", "table"), "列出 workspace 系统服务状态"
        )
    if service == "4":
        return GuidedCommand(("system", "doctor"), "诊断 socket、健康文件和锁")
    if service == "5":
        return GuidedCommand(
            ("system", "repair"), "清理确认 stale 的运行资源", dangerous=True
        )

    component = "reference" if service == "1" else "market"
    context.selected_service = component
    action = _prompt_menu(
        f"你想对 {component} 做什么？",
        (
            ("1", "查看状态"),
            ("2", "启动"),
            ("3", "停止"),
            ("4", "重启"),
            ("5", "查看日志"),
        ),
    )
    if action == "1":
        return GuidedCommand(
            ("system", "status", "--component", component, "--format", "text"),
            f"查看 {component} 状态",
        )
    if action == "2":
        return GuidedCommand(
            ("system", "up", "--component", component, "--format", "text"),
            f"启动 {component}",
            True,
        )
    if action == "3":
        return GuidedCommand(
            ("system", "down", "--component", component, "--format", "text"),
            f"停止 {component}",
            True,
        )
    if action == "4":
        return GuidedCommand(
            ("system", "restart", "--component", component, "--format", "text"),
            f"重启 {component}",
            True,
        )
    return GuidedCommand(
        ("system", "logs", "--component", component), f"查看 {component} 日志"
    )


def _convenience_workflow(context: InteractiveContext) -> GuidedCommand:
    choice = _prompt_menu(
        "你想查询或操作什么？",
        (
            ("1", "账户列表"),
            ("2", "账户余额"),
            ("3", "账户持仓"),
            ("4", "行情快照"),
            ("5", "订单状态"),
            ("6", "Reference 查询"),
            ("7", "期权链"),
            ("8", "通知配置校验"),
            ("9", "Provider 集成帮助"),
        ),
    )
    if choice == "1":
        return GuidedCommand(("account", "list", "--output", "table"), "查看已配置账户")
    if choice in {"2", "3"}:
        account = typer.prompt("账户 id", default="demo-paper").strip()
        command = "balances" if choice == "2" else "positions"
        return GuidedCommand(
            ("account", "--account-id", account, command, "--output", "table"),
            "查看账户事实",
        )
    if choice == "4":
        kind = _prompt_component(
            "快照类型", default="quote", choices=("quote", "bar", "greeks")
        )
        market_id = typer.prompt(
            "market id", default="market:binance:spot:BTCUSDT"
        ).strip()
        source_id = typer.prompt("source id", default="binance-spot").strip()
        return GuidedCommand(
            (
                "market",
                "snapshot",
                kind,
                "--market-id",
                market_id,
                "--source-id",
                source_id,
                "--format",
                "table",
            ),
            "读取运行中 Market 快照",
        )
    if choice == "5":
        order_id = typer.prompt("order id", default="").strip()
        if not order_id:
            raise typer.BadParameter("order id 不能为空")
        return GuidedCommand(
            ("order", "status", "--order-id", order_id, "--output", "text"),
            "查看订单状态",
        )
    if choice == "6":
        return _reference_workflow(context)
    if choice == "7":
        underlying = typer.prompt(
            "underlying instrument id",
            default="instrument:equity:US:AAPL:common",
        ).strip()
        return GuidedCommand(
            (
                "reference",
                "option-chain",
                "--underlying-instrument-id",
                underlying,
                "--format",
                "table",
            ),
            "查询期权链",
        )
    if choice == "8":
        return GuidedCommand(
            ("notifications", "validate", "--format", "text"), "校验通知目的地"
        )
    return GuidedCommand(
        ("integration", "--help"),
        "查看 Provider 集成入口；真实调用通常需要凭据",
        needs_workspace=False,
    )


def _reference_workflow(context: InteractiveContext) -> GuidedCommand:
    choice = _prompt_menu(
        "你想查询 Reference 里的什么？",
        (
            ("1", "当前系统有哪些 market"),
            ("2", "某个 asset/symbol 相关的 market"),
            ("3", "有哪些 listing"),
            ("4", "某个 market 的信息"),
            ("5", "按 symbol 检索 instrument / market / listing"),
        ),
    )
    if choice == "1":
        limit = typer.prompt("最多显示多少个 market", default="50").strip()
        return GuidedCommand(
            (
                "reference",
                "markets",
                "--active-only",
                "--limit",
                limit,
                "--format",
                "table",
            ),
            "查看当前可用 market",
        )
    if choice == "2":
        asset_code = typer.prompt(
            "asset code / symbol", default=context.reference_asset_code or "AAPL"
        ).strip()
        context.reference_asset_code = asset_code
        return GuidedCommand(
            (
                "reference",
                "markets",
                "--asset-code",
                asset_code,
                "--active-only",
                "--format",
                "table",
            ),
            f"查看 {asset_code} 相关的 market",
        )
    if choice == "3":
        symbol = typer.prompt("symbol（可留空）", default="").strip()
        argv = ("reference", "listings", "--active-only", "--format", "table")
        if symbol:
            argv = (*argv, "--symbol", symbol)
        return GuidedCommand(argv, "查看 listing")
    if choice == "4":
        market_id = typer.prompt(
            "market id", default="market:binance:spot:BTCUSDT"
        ).strip()
        return GuidedCommand(
            ("reference", "markets", "--market-id", market_id, "--format", "text"),
            "查看指定 market 信息",
        )
    symbol = typer.prompt("symbol", default="BTCUSDT").strip()
    target = _prompt_menu(
        "你想在哪类对象里检索？",
        (
            ("1", "instrument"),
            ("2", "market"),
            ("3", "listing"),
        ),
    )
    if target == "1":
        return GuidedCommand(
            ("reference", "instruments", "--symbol", symbol, "--format", "table"),
            "按 symbol 检索 instrument",
        )
    if target == "2":
        return GuidedCommand(
            ("reference", "markets", "--symbol", symbol, "--format", "table"),
            "按 symbol 检索 market",
        )
    return GuidedCommand(
        ("reference", "listings", "--symbol", symbol, "--format", "table"),
        "按 symbol 检索 listing",
    )


def _data_research_workflow() -> GuidedCommand:
    choice = _prompt_menu(
        "你想处理哪类数据或研究动作？",
        (
            ("1", "列出 datasets"),
            ("2", "审阅 data requirements"),
            ("3", "执行 data requirements"),
            ("4", "列出 dataset set aliases"),
            ("5", "锁定 research plan"),
            ("6", "发布 research gate"),
        ),
    )
    if choice == "1":
        return GuidedCommand(("data", "list"), "列出项目 datasets")
    if choice in {"2", "3"}:
        path = typer.prompt(
            "requirements.json 路径", default="requirements.json"
        ).strip()
        if choice == "2":
            return GuidedCommand(("data", "plan", path), "审阅数据需求，不下载")
        plan_hash = typer.prompt("expected plan hash", default="").strip()
        argv = ("data", "execute", path)
        if plan_hash:
            argv = (*argv, "--expected-plan-hash", plan_hash)
        return GuidedCommand(argv, "执行已经审阅的数据计划", dangerous=True)
    if choice == "4":
        return GuidedCommand(("data", "set", "list"), "列出 Dataset Set aliases")
    if choice == "5":
        path = typer.prompt(
            "research-plan.json 路径", default="research-plan.json"
        ).strip()
        return GuidedCommand(("research", "plan", "lock", path), "锁定研究计划")
    plan = typer.prompt("research-plan.json 路径", default="research-plan.json").strip()
    evidence = typer.prompt(
        "research-evidence.json 路径", default="research-evidence.json"
    ).strip()
    return GuidedCommand(
        ("research", "gate", "publish", plan, evidence),
        "发布研究 gate 证据",
        dangerous=True,
    )


def _diagnose_workflow(owner, snapshot: ObserveSnapshot | None) -> GuidedCommand:
    if owner is None:
        return GuidedCommand(
            ("project", "init"), "创建或初始化 Kairos 项目", True, False
        )
    if snapshot is None or snapshot.error:
        return GuidedCommand(("project", "doctor"), "检查项目 readiness")
    if not _launch_ids(owner, snapshot):
        return GuidedCommand(("project", "doctor"), "检查项目是否缺少 launch 配置")
    suggested = recommended_action(snapshot)
    typer.echo(f"我建议先运行：{suggested}")
    if typer.confirm("使用这条建议命令吗？", default=True):
        return GuidedCommand(tuple(shlex.split(suggested)[1:]), "执行推荐排障命令")
    return GuidedCommand(("system", "doctor"), "检查系统运行资源")


def _prompt_launch_id(
    owner, snapshot: ObserveSnapshot | None, selected_launch: str | None = None
) -> str:
    launch_ids = _launch_ids(owner, snapshot)
    if launch_ids:
        typer.echo("可用 launch：")
        for index, launch_id in enumerate(launch_ids, start=1):
            typer.echo(f"  {index}. {launch_id}")
        default = (
            str(launch_ids.index(selected_launch) + 1)
            if selected_launch in launch_ids
            else "1"
        )
        value = typer.prompt(
            "选择 launch 序号或直接输入 launch id", default=default
        ).strip()
        if value.isdigit() and 1 <= int(value) <= len(launch_ids):
            return launch_ids[int(value) - 1]
        return value
    return typer.prompt("launch id", default="demo-backtest").strip()


def _launch_ids(owner, snapshot: ObserveSnapshot | None) -> tuple[str, ...]:
    values: list[str] = []
    if snapshot is not None:
        for item in snapshot.launches:
            launch_id = str(item.get("launch_id") or "").strip()
            if launch_id and launch_id not in values:
                values.append(launch_id)
    if owner is not None:
        config_dir = owner.paths.config / "launches"
        if config_dir.is_dir():
            for path in sorted(config_dir.glob("*.toml")):
                if path.stem not in values:
                    values.append(path.stem)
        try:
            for item in LaunchRegistryApplication(owner).list():
                launch_id = str(item.get("launch_id") or "").strip()
                if launch_id and launch_id not in values:
                    values.append(launch_id)
        except Exception:
            pass
    return tuple(values)


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


def _prompt_component(label: str, *, default: str, choices: tuple[str, ...]) -> str:
    text = typer.prompt(f"{label}（{'/'.join(choices)}）", default=default).strip()
    if text not in choices:
        raise typer.BadParameter(f"{label} 必须是：{', '.join(choices)}")
    return text


def _with_workspace(command: GuidedCommand, workspace: Path | None) -> tuple[str, ...]:
    argv = command.argv
    if not command.needs_workspace or workspace is None:
        return argv
    if "--workspace" in argv or any(item.startswith("--workspace=") for item in argv):
        return argv
    return (*argv, "--workspace", str(workspace))


def _display_command(argv: Sequence[str]) -> str:
    return "kairos " + shlex.join(tuple(argv))
