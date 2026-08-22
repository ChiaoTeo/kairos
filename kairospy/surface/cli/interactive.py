from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass
from enum import Enum
from io import StringIO
from pathlib import Path
import shlex
import sys
from typing import Any

from prettytable import PrettyTable
import typer

from kairospy.application.account.cli import AccountCliApplication
from kairospy.application.launch.application import LaunchRegistryApplication
from kairospy.application.system import ComponentProcessApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli.activity import TerminalActivity
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
    streaming: bool = False


ShellAction = GuidedCommand | ShellControl | None


@dataclass(slots=True)
class InteractiveContext:
    owner: Any | None
    snapshot: ObserveSnapshot | None
    workspace_arg: Path | None
    selected_launch: str | None = None
    selected_account: str | None = None
    selected_service: str | None = None
    selected_reference: Any | None = None
    selected_reference_kind: str | None = None
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
        if context.shell_path:
            typer.echo("  b. 返回上一级")
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
            if len(context.shell_path) == 2 and context.shell_path[0] == "account":
                _print_selected_account(context)
            elif len(context.shell_path) == 2 and context.shell_path[0] == "launch":
                _print_selected_launch(context)
            elif context.selected_reference is not None:
                _print_reference_detail(context, technical=False)
            else:
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
            context.selected_account = None
            context.selected_launch = None
            context.selected_reference = None
            context.selected_reference_kind = None
            continue
        if line in {"back", "b"}:
            context.shell_path = context.shell_path[:-1]
            if not context.shell_path or context.shell_path == ("account",):
                context.selected_account = None
            if not context.shell_path or context.shell_path == ("launch",):
                context.selected_launch = None
            if context.shell_path != ("system",):
                context.selected_service = None
            if context.selected_reference is not None:
                context.selected_reference = None
                context.selected_reference_kind = None
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
                    "  1. 账户",
                    "  2. 策略运行",
                    "  3. 市场目录",
                    "  4. 行情",
                    "  5. 数据与研究",
                    "  6. 系统状态",
                    "  7. 诊断与观测",
                    "  8. 命令帮助",
                )
            )
        )
        return
    if path == ("launch",):
        typer.echo("策略运行：")
        _print_launch_list(context)
        typer.echo("输入序号选择并进入 launch；refresh 刷新列表。")
        return
    if len(path) == 2 and path[0] == "launch":
        typer.echo(
            "\n".join(
                (
                    f"当前 launch：{path[1]}",
                    "  1. 概览",
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
    if path and path[0] == "reference":
        _print_reference_menu(context)
        return
    if path == ("market",):
        typer.echo(
            "\n".join(
                (
                    "行情：",
                    "  1. 实时报价",
                    "  2. K 线快照",
                    "  3. Greeks 快照",
                    "  4. 查看行情新鲜度",
                )
            )
        )
        return
    if path == ("account",):
        typer.echo("账户：")
        _print_account_list(context)
        typer.echo("输入序号选择并进入账户；refresh 刷新列表。")
        return
    if len(path) == 2 and path[0] == "account":
        typer.echo(
            "\n".join(
                (
                    f"当前账户：{path[1]}",
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
                    "  account             进入账户",
                    "  launch              进入策略运行",
                    "  reference           进入市场目录",
                    "  market              进入行情",
                    "  data                进入数据与研究",
                    "  system              进入系统状态",
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
                    "  <序号>              选择并进入 launch",
                    "  select              按序号或 ID 选择 launch",
                    "  list                重新显示 launch 列表",
                    "  back / b            返回上一级",
                    "  home                回到根上下文",
                )
            )
        )
        return
    if len(path) == 2 and path[0] == "launch":
        typer.echo(
            "\n".join(
                (
                    f"当前 launch：{path[1]}",
                    "可用命令：",
                    "  summary             显示 launch 概览",
                    "  start/status/logs/attach/wait/stop/validate/edit/report",
                    "  back / b            返回 /launch",
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
                    "  back / b            返回上一级",
                    "  home                回到根上下文",
                )
            )
        )
        return
    if path and path[0] == "reference":
        typer.echo("输入代码或名称检索；list 浏览；summary 查看详情；back 返回。")
        return
    if path == ("market",):
        typer.echo("可用命令：quote/bar/greeks/freshness/back/home/exit")
        return
    if path == ("account",):
        typer.echo("可用命令：<序号>/select/list/back/home/exit")
        return
    if len(path) == 2 and path[0] == "account":
        typer.echo(
            "可用命令：summary/assets/positions/earn/open-orders/fees/transfer/settings/switch"
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
                    "  back / b            返回 /system",
                    "  home                回到根上下文",
                )
            )
        )
        return
    typer.echo("输入 back 或 b 返回上一级，home 回到根上下文，exit 退出。")


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
    if len(path) == 2 and path[0] == "launch":
        return _launch_shell_command(context, parts)
    if path == ("system",):
        return _system_shell_command(context, parts)
    if path and path[0] == "reference":
        return _reference_shell_command(context, parts)
    if path == ("market",):
        return _market_shell_command(context, parts)
    if path == ("account",) or (len(path) == 2 and path[0] == "account"):
        return _account_shell_command(context, parts)
    if path == ("data",):
        return _data_shell_command(parts)
    if path in {("system", "reference"), ("system", "market")}:
        return _system_service_shell_command(context, parts)
    return None


def _root_shell_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if parts in {("1",), ("account",)}:
        context.shell_path = ("account",)
        return ShellControl.HANDLED
    if parts in {("2",), ("launch",)}:
        context.shell_path = ("launch",)
        return ShellControl.HANDLED
    if parts in {("3",), ("target",), ("targets",), ("reference",)}:
        context.shell_path = ("reference",)
        return ShellControl.HANDLED
    if parts in {("4",), ("market",), ("quotes",)}:
        context.shell_path = ("market",)
        return ShellControl.HANDLED
    if parts in {("5",), ("data",), ("research",)}:
        context.shell_path = ("data",)
        return ShellControl.HANDLED
    if parts in {("6",), ("system",)}:
        context.shell_path = ("system",)
        return ShellControl.HANDLED
    if parts == ("system", "reference"):
        context.shell_path = ("system", "reference")
        context.selected_service = "reference"
        return ShellControl.HANDLED
    if parts == ("system", "market"):
        context.shell_path = ("system", "market")
        context.selected_service = "market"
        return ShellControl.HANDLED
    if parts in {("8",), ("quickstart",), ("map",), ("help",)}:
        return GuidedCommand(
            ("quickstart",), "查看 CLI 场景地图", needs_workspace=False
        )
    if parts in {("7",), ("doctor",), ("observe",)}:
        return GuidedCommand(("observe",), "打开项目观测台", streaming=True)
    return None


def _launch_shell_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if context.shell_path == ("launch",):
        launch_ids = _launch_ids(context.owner, context.snapshot)
        if len(parts) == 1 and parts[0].isdigit():
            index = int(parts[0])
            if not 1 <= index <= len(launch_ids):
                typer.echo(f"找不到 launch 序号：{parts[0]}")
                return ShellControl.HANDLED
            launch_id = launch_ids[index - 1]
            context.selected_launch = launch_id
            context.selected_account = None
            context.shell_path = ("launch", launch_id)
            _print_selected_launch(context)
            return ShellControl.HANDLED
        if parts in {("select",), ("enter",)}:
            launch_id = _prompt_launch_id(
                context.owner, context.snapshot, context.selected_launch
            )
            if launch_id in {"b", "back"}:
                return ShellControl.HANDLED
            context.selected_launch = launch_id
            context.selected_account = None
            context.shell_path = ("launch", launch_id)
            _print_selected_launch(context)
            return ShellControl.HANDLED
        if parts in {("list",), ("ls",)}:
            _print_launch_list(context)
            return ShellControl.HANDLED
        return None

    launch_id = context.shell_path[1]
    context.selected_launch = launch_id
    if parts in {("1",), ("summary",), ("overview",)}:
        _print_selected_launch(context)
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
    return GuidedCommand(
        ("launch", *tuple(action.split()), launch_id),
        summary,
        dangerous=dangerous,
        streaming=action == "attach",
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
) -> ShellAction:
    path = context.shell_path
    if context.selected_reference is not None:
        return _reference_detail_command(context, parts)

    key = parts[0] if len(parts) == 1 else ""
    if path == ("reference",):
        routes = {
            "1": ("reference", "assets"),
            "assets": ("reference", "assets"),
            "5": ("reference", "instruments"),
            "instruments": ("reference", "instruments"),
            "6": ("reference", "markets"),
            "markets": ("reference", "markets"),
        }
        participant_routes = {
            "2": ("exchanges", "exchange", "交易所"),
            "exchanges": ("exchanges", "exchange", "交易所"),
            "3": ("brokers", "broker", "券商"),
            "brokers": ("brokers", "broker", "券商"),
            "4": ("providers", "data_provider", "数据提供商"),
            "providers": ("providers", "data_provider", "数据提供商"),
        }
        participant = participant_routes.get(key)
        if participant is not None:
            route, entity_type, label = participant
            context.shell_path = ("reference", "participants", route)
            _reference_search_and_select(
                context, ("entity", label, entity_type), query=None
            )
            return ShellControl.HANDLED
        route = routes.get(key)
        if route is None:
            return None
        context.shell_path = route
        return ShellControl.HANDLED

    if path == ("reference", "instruments"):
        routes = {
            "1": "equities",
            "equities": "equities",
            "2": "spot",
            "spot": "spot",
            "3": "perpetuals",
            "perpetuals": "perpetuals",
            "4": "futures",
            "futures": "futures",
            "5": "options",
            "options": "options",
            "6": "indices",
            "indices": "indices",
        }
        category = routes.get(key)
        if category is None:
            return None
        context.shell_path = (*path, category)
        return ShellControl.HANDLED

    collection = _reference_collection(path)
    if collection is None:
        return None
    query = " ".join(parts).strip()
    if collection[0] == "entity" and query in {"refresh", "list", "ls"}:
        query = ""
    if query in {"search", "find"}:
        query = typer.prompt("输入代码或名称").strip()
    elif query in {"list", "ls"}:
        query = ""
    if not query and parts[0] not in {"list", "ls", "refresh"}:
        typer.echo("请输入代码或名称；输入 list 可浏览前 10 条。")
        return ShellControl.HANDLED
    _reference_search_and_select(context, collection, query or None)
    return ShellControl.HANDLED


_REFERENCE_INSTRUMENT_TYPES = {
    "equities": ("equity", "股票"),
    "spot": ("spot", "现货"),
    "perpetuals": ("perpetual", "永续合约"),
    "futures": ("future", "交割合约"),
    "options": ("option", "期权"),
    "indices": ("index", "指数"),
}

_REFERENCE_PARTICIPANT_TYPES = {
    "exchanges": ("exchange", "交易所"),
    "brokers": ("broker", "券商"),
    "providers": ("data_provider", "数据提供商"),
}


def _print_reference_menu(context: InteractiveContext) -> None:
    path = context.shell_path
    if context.selected_reference is not None:
        label = _reference_record_label(
            context.selected_reference_kind, context.selected_reference
        )
        actions = {
            "asset": ("  1. 概览", "  2. 相关市场", "  3. 技术标识"),
            "instrument": (
                "  1. 概览",
                "  2. 上市信息",
                "  3. 具体市场",
                "  4. 技术标识",
            ),
            "market": ("  1. 概览", "  2. 技术标识"),
            "entity": (
                "  1. 概览",
                "  2. 上市信息或市场",
                "  3. 技术标识",
            ),
        }.get(context.selected_reference_kind or "", ("  1. 概览",))
        typer.echo("\n".join((f"当前：{label}", *actions)))
        return
    if path == ("reference",):
        typer.echo(
            "\n".join(
                (
                    "Reference 市场目录：",
                    "  1. 资产",
                    "  2. 交易所",
                    "  3. 券商",
                    "  4. 数据提供商",
                    "  5. 交易品种",
                    "  6. 具体市场",
                )
            )
        )
        return
    if path == ("reference", "instruments"):
        typer.echo(
            "\n".join(
                (
                    "交易品种：",
                    "  1. 股票",
                    "  2. 现货",
                    "  3. 永续合约",
                    "  4. 交割合约",
                    "  5. 期权",
                    "  6. 指数",
                )
            )
        )
        return
    collection = _reference_collection(path)
    if collection is not None:
        if collection[0] == "entity":
            typer.echo(f"{collection[1]}：输入 refresh 重新读取列表。")
        else:
            typer.echo(f"{collection[1]}：输入代码或名称检索；输入 list 浏览前 10 条。")


def _reference_collection(path: tuple[str, ...]) -> tuple[str, str, str | None] | None:
    if path == ("reference", "assets"):
        return ("asset", "资产", None)
    if path == ("reference", "markets"):
        return ("market", "具体市场", None)
    if len(path) == 3 and path[:2] == ("reference", "participants"):
        participant = _REFERENCE_PARTICIPANT_TYPES.get(path[2])
        if participant is not None:
            return ("entity", participant[1], participant[0])
    if len(path) == 3 and path[:2] == ("reference", "instruments"):
        instrument = _REFERENCE_INSTRUMENT_TYPES.get(path[2])
        if instrument is not None:
            return ("instrument", instrument[1], instrument[0])
    return None


def _reference_application(context: InteractiveContext):
    if context.owner is None:
        raise RuntimeError("当前没有可用的 workspace")
    from kairospy.application.reference import ReferenceApplication
    from kairospy.infrastructure.contracts.reference import ReferenceClient

    return ReferenceApplication(
        ReferenceClient(database_path=context.owner.paths.reference_database())
    )


def _reference_search_and_select(
    context: InteractiveContext,
    collection: tuple[str, str, str | None],
    query: str | None,
) -> None:
    kind, label, subtype = collection
    try:
        app = _reference_application(context)
        if kind == "asset":
            records = app.find_assets(query=query, active_only=True, limit=25)
        elif kind == "entity":
            records = app.find_entities(
                query=query, entity_type=subtype, active_only=True, limit=25
            )
        elif kind == "instrument":
            records = app.find_instruments(
                query=query, instrument_type=subtype, active_only=True, limit=25
            )
        else:
            records = app.find_markets(query=query, active_only=True, limit=25)
    except Exception as error:
        typer.echo(f"读取 Reference 目录失败：{error}")
        return

    ranked = _rank_reference_records(kind, tuple(records), query)[:10]
    if not ranked:
        suffix = f"“{query}”" if query else "当前分类"
        typer.echo(f"没有找到与{suffix}匹配的{label}。")
        return
    _render_reference_results(kind, ranked)
    choice = typer.prompt("输入序号查看详情；输入 b 返回", default="b").strip()
    if choice in {"b", "back", ""}:
        return
    if not choice.isdigit() or not 1 <= int(choice) <= len(ranked):
        typer.echo("无效的结果序号。")
        return
    selected = ranked[int(choice) - 1]
    context.selected_reference = selected
    context.selected_reference_kind = kind
    context.shell_path = (*context.shell_path, _reference_record_slug(kind, selected))
    _print_reference_detail(context, technical=False)


def _rank_reference_records(
    kind: str, records: tuple[Any, ...], query: str | None
) -> tuple[Any, ...]:
    if not query:
        return records
    expected = query.casefold()

    def rank(record: Any) -> tuple[int, str]:
        values = _reference_search_values(kind, record)
        lowered = tuple(value.casefold() for value in values if value)
        if expected in lowered:
            score = 0
        elif any(value.startswith(expected) for value in lowered):
            score = 1
        else:
            score = 2
        return (score, lowered[0] if lowered else "")

    return tuple(sorted(records, key=rank))


def _reference_search_values(kind: str, record: Any) -> tuple[str, ...]:
    if kind == "asset":
        return (record.code, record.name or "", str(record.id))
    if kind == "entity":
        return (record.name, str(record.id))
    if kind == "instrument":
        return (record.symbol, record.name or "", str(record.id))
    return (record.venue_symbol or "", record.instrument.display_symbol, str(record.id))


def _render_reference_results(kind: str, records: Sequence[Any]) -> None:
    if kind == "asset":
        table = PrettyTable(["序号", "代码", "名称", "类型", "状态"])
        for index, record in enumerate(records, 1):
            table.add_row(
                [
                    index,
                    record.code,
                    record.name or "—",
                    _asset_class_label(record.asset_class),
                    _status_label(record.status),
                ]
            )
    elif kind == "entity":
        table = PrettyTable(["序号", "名称", "类型", "状态"])
        for index, record in enumerate(records, 1):
            table.add_row(
                [
                    index,
                    record.name,
                    _entity_type_label(record.entity_type),
                    _status_label(record.status),
                ]
            )
    elif kind == "instrument":
        table = PrettyTable(["序号", "代码", "名称", "类型", "状态"])
        for index, record in enumerate(records, 1):
            table.add_row(
                [
                    index,
                    record.symbol,
                    record.name or "—",
                    _instrument_type_label(record.instrument_type),
                    _status_label(record.status),
                ]
            )
    elif kind == "listing":
        table = PrettyTable(["序号", "交易所", "代码", "状态"])
        for index, record in enumerate(records, 1):
            table.add_row(
                [
                    index,
                    _short_id(record.exchange_id),
                    record.exchange_symbol,
                    _status_label(record.status),
                ]
            )
    else:
        table = PrettyTable(["序号", "代码", "交易所", "类型", "计价资产", "状态"])
        for index, record in enumerate(records, 1):
            table.add_row(
                [
                    index,
                    record.venue_symbol or record.instrument.display_symbol,
                    _short_id(record.exchange_id),
                    _instrument_type_label(record.instrument_kind),
                    _short_id(record.quote_asset),
                    _status_label(record.status),
                ]
            )
    table.align = "l"
    typer.echo(table)


def _reference_record_slug(kind: str, record: Any) -> str:
    if kind == "asset":
        return record.code
    if kind == "entity":
        return _short_id(record.id)
    if kind == "instrument":
        return record.symbol
    return record.venue_symbol or _short_id(record.id)


def _reference_record_label(kind: str | None, record: Any) -> str:
    if kind == "asset":
        return (
            f"{record.code} · {record.name or _asset_class_label(record.asset_class)}"
        )
    if kind == "entity":
        return f"{record.name} · {_entity_type_label(record.entity_type)}"
    if kind == "instrument":
        return f"{record.symbol} · {_instrument_type_label(record.instrument_type)}"
    return f"{record.venue_symbol or record.instrument.display_symbol} · {_short_id(record.exchange_id)}"


def _print_reference_detail(context: InteractiveContext, *, technical: bool) -> None:
    record = context.selected_reference
    kind = context.selected_reference_kind
    if record is None or kind is None:
        typer.echo("请先选择一个 Reference 目录对象。")
        return
    table = PrettyTable(["项目", "值"])
    table.align = "l"
    if kind == "asset":
        table.add_row(["代码", record.code])
        table.add_row(["名称", record.name or "—"])
        table.add_row(["资产类型", _asset_class_label(record.asset_class)])
        table.add_row(["状态", _status_label(record.status)])
        if technical:
            table.add_row(["Asset ID", record.id])
    elif kind == "entity":
        table.add_row(["名称", record.name])
        table.add_row(["参与方类型", _entity_type_label(record.entity_type)])
        table.add_row(["状态", _status_label(record.status)])
        if technical:
            table.add_row(["Entity ID", record.id])
    elif kind == "instrument":
        table.add_row(["代码", record.symbol])
        table.add_row(["名称", record.name or "—"])
        table.add_row(["品种类型", _instrument_type_label(record.instrument_type)])
        table.add_row(["状态", _status_label(record.status)])
        if record.expiry_unix_nanos is not None:
            table.add_row(["到期时间", record.expiry_unix_nanos])
        if record.strike is not None:
            table.add_row(["行权价", record.strike])
        if record.option_right is not None:
            table.add_row(
                ["期权方向", "看涨" if record.option_right == "call" else "看跌"]
            )
        if technical:
            table.add_row(["Instrument ID", record.id])
            table.add_row(["Underlying ID", record.underlying_instrument_id or "—"])
    else:
        table.add_row(["代码", record.venue_symbol or record.instrument.display_symbol])
        table.add_row(["交易所", _short_id(record.exchange_id)])
        table.add_row(["市场类型", _instrument_type_label(record.instrument_kind)])
        table.add_row(["基础资产", _short_id(record.base_asset)])
        table.add_row(["计价资产", _short_id(record.quote_asset)])
        table.add_row(["状态", _status_label(record.status)])
        if technical:
            table.add_row(["Market ID", record.id])
            table.add_row(["Instrument ID", record.instrument.id])
            table.add_row(["Listing ID", record.listing_id or "—"])
    typer.echo(table)


def _reference_detail_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if len(parts) != 1:
        return None
    key = parts[0]
    kind = context.selected_reference_kind
    record = context.selected_reference
    if key in {"1", "summary", "overview"}:
        _print_reference_detail(context, technical=False)
        return ShellControl.HANDLED
    if kind == "asset" and key in {"2", "markets"}:
        _render_related_reference(
            context, "market", asset_code=record.code, active_only=True, limit=10
        )
        return ShellControl.HANDLED
    if kind == "asset" and key in {"3", "technical"}:
        _print_reference_detail(context, technical=True)
        return ShellControl.HANDLED
    if kind == "instrument" and key in {"2", "listings"}:
        _render_related_reference(
            context, "listing", instrument_id=record.id, active_only=True, limit=10
        )
        return ShellControl.HANDLED
    if kind == "instrument" and key in {"3", "markets"}:
        _render_related_reference(
            context, "market", instrument_id=record.id, active_only=True, limit=10
        )
        return ShellControl.HANDLED
    if kind == "instrument" and key in {"4", "technical"}:
        _print_reference_detail(context, technical=True)
        return ShellControl.HANDLED
    if kind == "entity" and key in {"2", "related"}:
        if record.entity_type == "exchange":
            _render_related_reference(
                context, "listing", exchange=record.id, active_only=True, limit=10
            )
        else:
            typer.echo("当前目录没有这个参与方的下级 Reference 记录。")
        return ShellControl.HANDLED
    if kind == "entity" and key in {"3", "technical"}:
        _print_reference_detail(context, technical=True)
        return ShellControl.HANDLED
    if kind == "market" and key in {"2", "technical"}:
        _print_reference_detail(context, technical=True)
        return ShellControl.HANDLED
    return None


def _render_related_reference(
    context: InteractiveContext, kind: str, **filters: Any
) -> None:
    try:
        app = _reference_application(context)
        records = (
            app.find_listings(**filters)
            if kind == "listing"
            else app.find_markets(**filters)
        )
    except Exception as error:
        typer.echo(f"读取关联 Reference 记录失败：{error}")
        return
    if not records:
        typer.echo("没有找到关联记录。")
        return
    _render_reference_results(kind, records)


def _short_id(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).rsplit(":", 1)[-1]


def _status_label(value: Any) -> str:
    labels = {
        "active": "有效",
        "trading": "交易中",
        "inactive": "停用",
        "halted": "暂停",
        "delisted": "已退市",
        "unknown": "未知",
    }
    return labels.get(str(value), str(value))


def _asset_class_label(value: str) -> str:
    return {"fiat": "法币", "crypto": "加密资产", "equity": "股票资产"}.get(
        value, value
    )


def _entity_type_label(value: str) -> str:
    return {"exchange": "交易所", "broker": "券商", "data_provider": "数据提供商"}.get(
        value, value
    )


def _instrument_type_label(value: str) -> str:
    return {
        "equity": "股票",
        "spot": "现货",
        "perpetual": "永续合约",
        "future": "交割合约",
        "option": "期权",
        "index": "指数",
    }.get(value, value)


def _market_shell_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    if len(parts) != 1:
        return None
    key = parts[0]
    if key not in {"1", "quote", "2", "bar", "3", "greeks", "4", "freshness"}:
        return None

    market_id = typer.prompt("market id", default="market:binance:spot:BTCUSDT").strip()
    source_id = typer.prompt("source id", default="binance-spot").strip()
    if not market_id or not source_id:
        raise typer.BadParameter("market id 和 source id 不能为空")

    if key in {"4", "freshness"}:
        return GuidedCommand(
            (
                "system",
                "component",
                "market",
                "freshness",
                "--market-id",
                market_id,
                "--source-id",
                source_id,
                "--format",
                "table",
            ),
            "查看运行中行情的新鲜度",
        )

    kind = {
        "1": "quote",
        "quote": "quote",
        "2": "bar",
        "bar": "bar",
        "3": "greeks",
        "greeks": "greeks",
    }[key]
    argv = (
        "system",
        "component",
        "market",
        "snapshot",
        kind,
        "--market-id",
        market_id,
        "--source-id",
        source_id,
    )
    if kind == "bar":
        timeframe = typer.prompt("timeframe", default="1m").strip()
        if not timeframe:
            raise typer.BadParameter("timeframe 不能为空")
        argv = (*argv, "--timeframe", timeframe)
    return GuidedCommand((*argv, "--format", "table"), f"读取当前 {kind} 行情快照")


def _query_shell_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    key = parts[0]
    if len(parts) != 1:
        return None
    if key in {"1", "account", "accounts"}:
        context.shell_path = ("account",)
        return ShellControl.HANDLED
    if key in {"2", "snapshot"}:
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
    if key in {"3", "order"}:
        order_id = typer.prompt("order id", default="").strip()
        if not order_id:
            raise typer.BadParameter("order id 不能为空")
        return GuidedCommand(
            ("order", "status", "--order-id", order_id, "--output", "text"),
            "查看订单状态",
        )
    if key in {"4", "notifications"}:
        return GuidedCommand(
            ("notifications", "validate", "--format", "text"), "校验通知目的地"
        )
    if key in {"5", "integration"}:
        return GuidedCommand(
            ("integration", "--help"),
            "查看 Provider 集成入口；真实调用通常需要凭据",
            needs_workspace=False,
        )
    return None


def _account_shell_command(
    context: InteractiveContext, parts: tuple[str, ...]
) -> ShellAction:
    if len(parts) != 1:
        return None
    key = parts[0]
    if context.shell_path == ("account",):
        accounts = _account_records(context)
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
        return _account_fact_command(context, "overview", "查询账户概览")
    if key in {"2", "assets", "balances"}:
        return _account_fact_command(context, "assets", "查询账户资产与余额")
    if key in {"3", "positions"}:
        return _account_fact_command(context, "positions", "查询账户持仓")
    if key in {"4", "earn", "earn-holdings"}:
        return _account_fact_command(context, "earn-holdings", "查询理财与质押持有")
    if key in {"5", "open-orders", "orders"}:
        return _account_fact_command(context, "open-orders", "查询账户未完成订单")
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


def _account_fact_command(
    context: InteractiveContext, command: str, summary: str
) -> ShellAction:
    account_id = context.selected_account
    if account_id is None:
        typer.echo("请先选择账户。")
        return ShellControl.HANDLED
    return GuidedCommand(
        ("account", command, account_id, "--format", "table"),
        summary,
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


def _account_records(context: InteractiveContext) -> tuple[dict[str, Any], ...]:
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
    accounts = _account_records(context)
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
    values = accounts if accounts is not None else _account_records(context)
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
    _print_selected_account(context, accounts=accounts)


def _selected_account_record(context: InteractiveContext) -> dict[str, Any]:
    for account in _account_records(context):
        if account.get("account_id") == context.selected_account:
            return account
    return {}


def _print_selected_account(
    context: InteractiveContext,
    *,
    accounts: tuple[dict[str, Any], ...] | None = None,
) -> None:
    values = accounts if accounts is not None else _account_records(context)
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
    typer.echo()
    typer.echo(f"── {command.summary} ──")
    typer.echo(f"准备执行：{display}")
    typer.echo(f"用途：{command.summary}")
    if command.dangerous and not yes:
        typer.echo("这个动作可能改变运行状态。")
        if not typer.confirm("确认执行这个命令吗？", default=True):
            typer.echo("已取消。")
            typer.echo("── 已取消 ──")
            typer.echo()
            context.last_command = display
            context.last_status = 0
            return
    context.last_command = display
    status = _execute_with_activity(
        execute,
        argv,
        label=command.summary,
        enabled=not command.streaming,
    )
    context.last_status = status
    result = "完成" if status == 0 else "失败"
    typer.echo()
    typer.echo(f"── {result} · status={status} ──")
    typer.echo()
    _refresh_context(context)


def _execute_with_activity(
    execute: ExecuteCommand,
    argv: Sequence[str],
    *,
    label: str,
    enabled: bool,
) -> int:
    output = sys.stdout
    activity = TerminalActivity(label, output)
    if not enabled or not activity.enabled:
        return execute(argv)

    captured = StringIO()
    activity.start()
    try:
        with redirect_stdout(captured):
            status = execute(argv)
    except BaseException:
        activity.finish(succeeded=False)
        output.write(captured.getvalue())
        output.flush()
        raise
    activity.finish(succeeded=status == 0)
    output.write(captured.getvalue())
    output.flush()
    return status


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
    typer.echo(f"Workspace: {owner.workspace_id} · {owner.paths.project_root}")
    accounts = _account_records(context)
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
        str(value.get("state")) == "failed" for value in _unique_launches(snapshot)
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


def _context_table(context: InteractiveContext) -> str:
    table = PrettyTable(["上下文", "值"])
    table.align = "l"
    table.add_row(["account", context.selected_account or "-"])
    table.add_row(["launch", context.selected_launch or "-"])
    table.add_row(["system service", context.selected_service or "-"])
    reference_value = "-"
    if context.selected_reference is not None:
        reference_value = _reference_record_label(
            context.selected_reference_kind, context.selected_reference
        )
    table.add_row(["reference selection", reference_value])
    table.add_row(["last command", context.last_command or "-"])
    table.add_row(
        [
            "last status",
            "-" if context.last_status is None else str(context.last_status),
        ]
    )
    return str(table)


def _print_launch_list(context: InteractiveContext) -> None:
    launch_ids = _launch_ids(context.owner, context.snapshot)
    if not launch_ids:
        typer.echo("当前 workspace 没有可用 launch。")
        return
    records = {
        str(record.get("launch_id")): record
        for record in (
            _unique_launches(context.snapshot) if context.snapshot is not None else ()
        )
    }
    table = PrettyTable(["序号", "launch", "mode", "state", "instance"])
    table.align = "l"
    for index, launch_id in enumerate(launch_ids, start=1):
        record = records.get(launch_id, {})
        table.add_row(
            [
                index,
                launch_id,
                record.get("mode", "-"),
                record.get("state", "not_started"),
                record.get("instance_id", "-"),
            ]
        )
    typer.echo(table)


def _print_selected_launch(context: InteractiveContext) -> None:
    launch_id = context.selected_launch
    if launch_id is None:
        typer.echo("请先选择 launch。")
        return
    record = {}
    if context.snapshot is not None:
        record = next(
            (
                value
                for value in _unique_launches(context.snapshot)
                if value.get("launch_id") == launch_id
            ),
            {},
        )
    config = (
        context.owner.paths.launch_config(launch_id)
        if context.owner is not None
        else None
    )
    table = PrettyTable(["launch 上下文", "值"])
    table.align = "l"
    table.add_row(["launch", launch_id])
    table.add_row(["mode", record.get("mode", "-")])
    table.add_row(["state", record.get("state", "not_started")])
    table.add_row(["instance", record.get("instance_id", "-")])
    table.add_row(["config", str(config) if config is not None else "-"])
    typer.echo(table)


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
            ("4", "查询账户、行情、订单或市场目录"),
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
        return GuidedCommand(("observe",), "打开项目观测台", streaming=True)
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
    return GuidedCommand(
        argv,
        summary,
        dangerous=dangerous,
        streaming=argv[:2] == ("launch", "attach"),
    )


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
            ("6", "市场目录"),
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
        "你想查询市场目录中的什么？",
        (
            ("1", "资产"),
            ("2", "交易所"),
            ("3", "券商"),
            ("4", "数据提供商"),
            ("5", "交易品种"),
            ("6", "具体市场"),
        ),
    )
    if choice == "1":
        query = typer.prompt("输入资产代码或名称", default="BTC").strip()
        return GuidedCommand(
            (
                "reference",
                "assets",
                "--query",
                query,
                "--active-only",
                "--limit",
                "10",
                "--format",
                "table",
            ),
            f"检索资产 {query}",
        )
    if choice in {"2", "3", "4"}:
        participant = {
            "2": ("exchanges", "交易所"),
            "3": ("brokers", "券商"),
            "4": ("providers", "数据提供商"),
        }[choice]
        return GuidedCommand(
            (
                "reference",
                "participants",
                participant[0],
                "--format",
                "table",
            ),
            f"查看{participant[1]}",
        )
    if choice == "5":
        instrument_type = _prompt_menu(
            "请选择交易品种类型：",
            (
                ("1", "股票"),
                ("2", "现货"),
                ("3", "永续合约"),
                ("4", "交割合约"),
                ("5", "期权"),
                ("6", "指数"),
            ),
        )
        kind, label = {
            "1": ("equity", "股票"),
            "2": ("spot", "现货"),
            "3": ("perpetual", "永续合约"),
            "4": ("future", "交割合约"),
            "5": ("option", "期权"),
            "6": ("index", "指数"),
        }[instrument_type]
        query = typer.prompt("输入代码或名称", default="AAPL").strip()
        return GuidedCommand(
            (
                "reference",
                "markets",
                "--instrument-kind",
                kind,
                "--symbol",
                query,
                "--active-only",
                "--limit",
                "10",
                "--format",
                "table",
            ),
            f"检索{label} {query}",
        )
    symbol = typer.prompt("输入市场代码", default="BTCUSDT").strip()
    return GuidedCommand(
        (
            "reference",
            "markets",
            "--symbol",
            symbol,
            "--active-only",
            "--limit",
            "10",
            "--format",
            "table",
        ),
        f"检索具体市场 {symbol}",
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
