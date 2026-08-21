from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import shlex

import typer

from kairospy.application.launch.application import LaunchRegistryApplication
from kairospy.application.system import ComponentProcessApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.console.data import SystemObserveReader
from kairospy.surface.console.models import ObserveSnapshot, recommended_action


ExecuteCommand = Callable[[Sequence[str]], int]


@dataclass(frozen=True, slots=True)
class GuidedCommand:
    argv: tuple[str, ...]
    summary: str
    dangerous: bool = False
    needs_workspace: bool = True


def run_interactive(
    *,
    workspace: Path | None,
    dry_run: bool,
    no_exec: bool,
    yes: bool,
    execute: ExecuteCommand,
) -> int:
    """Run one guided CLI action and return the executed command status."""

    typer.echo("Kairos 交互式操作")
    typer.echo("选择你想完成的事情，Kairos 会引导你完成下一步。")
    typer.echo()

    owner = _workspace(workspace)
    snapshot = _snapshot(owner) if owner is not None else None
    _print_workspace_summary(owner, snapshot)

    command = _choose_command(owner, snapshot)
    argv = _with_workspace(command, workspace)
    typer.echo()
    typer.echo(f"准备执行：{_display_command(argv)}")
    typer.echo(f"用途：{command.summary}")

    if dry_run or no_exec:
        typer.echo("已开启 dry-run/no-exec，只展示命令，不执行。")
        return 0

    if command.dangerous and not yes:
        typer.echo("这个动作可能改变运行状态。")
    if not yes and not typer.confirm("确认执行这个命令吗？", default=True):
        typer.echo("已取消。")
        return 0
    return execute(argv)


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


def _print_workspace_summary(owner, snapshot: ObserveSnapshot | None) -> None:
    if owner is None:
        return
    typer.echo(f"Workspace：{owner.workspace_id}")
    typer.echo(f"路径：{owner.paths.project_root}")
    if snapshot is None:
        typer.echo("状态：暂时无法读取运行状态")
        typer.echo()
        return
    launches = ", ".join(
        f"{item.get('launch_id', '-')}/{item.get('state', 'unknown')}"
        for item in snapshot.launches[:3]
    )
    components = ", ".join(
        f"{name}={value.get('status', 'unknown')}"
        for name, value in sorted(snapshot.components.items())
    )
    typer.echo(f"Launch：{launches or '暂无运行记录'}")
    typer.echo(f"System：{components or '暂无组件状态'}")
    typer.echo(f"建议下一步：{recommended_action(snapshot)}")
    typer.echo()


def _choose_command(owner, snapshot: ObserveSnapshot | None) -> GuidedCommand:
    choice = _prompt_menu(
        "你想做什么？",
        (
            ("1", "从零开始创建项目并运行示例"),
            ("2", "运行或查看某个策略"),
            ("3", "维护系统运行组件"),
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
        return _strategy_workflow(owner, snapshot)
    if choice == "3":
        return _system_workflow()
    if choice == "4":
        return _convenience_workflow()
    if choice == "5":
        return _data_research_workflow()
    if choice == "6":
        return _diagnose_workflow(owner, snapshot)
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


def _strategy_workflow(owner, snapshot: ObserveSnapshot | None) -> GuidedCommand:
    launch_id = _prompt_launch_id(owner, snapshot)
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


def _system_workflow() -> GuidedCommand:
    action = _prompt_menu(
        "你想维护哪个系统动作？",
        (
            ("1", "列出组件"),
            ("2", "查看某个组件状态"),
            ("3", "启动 reference 或 market"),
            ("4", "停止 reference 或 market"),
            ("5", "重启 reference 或 market"),
            ("6", "查看组件日志"),
            ("7", "运行 system doctor"),
            ("8", "修复 stale 运行资源"),
        ),
    )
    if action == "1":
        return GuidedCommand(("system", "list"), "列出 workspace 组件状态")
    if action == "7":
        return GuidedCommand(("system", "doctor"), "诊断 socket、健康文件和锁")
    if action == "8":
        return GuidedCommand(
            ("system", "repair"), "清理确认 stale 的运行资源", dangerous=True
        )
    component = _prompt_component(
        "组件名",
        default="market",
        choices=("reference", "market", "account", "risk", "execution"),
    )
    if action == "2":
        return GuidedCommand(
            ("system", "status", "--component", component), "查看组件状态"
        )
    if action == "3":
        return GuidedCommand(
            ("system", "up", "--component", component), "启动 workspace 组件", True
        )
    if action == "4":
        return GuidedCommand(
            ("system", "down", "--component", component), "停止 workspace 组件", True
        )
    if action == "5":
        return GuidedCommand(
            ("system", "restart", "--component", component),
            "重启 workspace 组件",
            True,
        )
    return GuidedCommand(
        ("system", "logs", "--component", component), "查看组件日志"
    )


def _convenience_workflow() -> GuidedCommand:
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
        return GuidedCommand(
            ("account", "list", "--output", "table"), "查看已配置账户"
        )
    if choice in {"2", "3"}:
        account = typer.prompt("账户 id", default="demo-paper").strip()
        command = "balances" if choice == "2" else "positions"
        return GuidedCommand(
            ("account", "--account-id", account, command, "--output", "table"),
            "查看账户事实",
        )
    if choice == "4":
        kind = _prompt_component("快照类型", default="quote", choices=("quote", "bar", "greeks"))
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
        return _reference_workflow()
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


def _reference_workflow() -> GuidedCommand:
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
        asset_code = typer.prompt("asset code / symbol", default="AAPL").strip()
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
        path = typer.prompt("requirements.json 路径", default="requirements.json").strip()
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
        path = typer.prompt("research-plan.json 路径", default="research-plan.json").strip()
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
        return GuidedCommand(("project", "init"), "创建或初始化 Kairos 项目", True, False)
    if snapshot is None or snapshot.error:
        return GuidedCommand(("project", "doctor"), "检查项目 readiness")
    if not _launch_ids(owner, snapshot):
        return GuidedCommand(("project", "doctor"), "检查项目是否缺少 launch 配置")
    suggested = recommended_action(snapshot)
    typer.echo(f"我建议先运行：{suggested}")
    if typer.confirm("使用这条建议命令吗？", default=True):
        return GuidedCommand(tuple(shlex.split(suggested)[1:]), "执行推荐排障命令")
    return GuidedCommand(("system", "doctor"), "检查系统运行资源")


def _prompt_launch_id(owner, snapshot: ObserveSnapshot | None) -> str:
    launch_ids = _launch_ids(owner, snapshot)
    if launch_ids:
        typer.echo("可用 launch：")
        for index, launch_id in enumerate(launch_ids, start=1):
            typer.echo(f"  {index}. {launch_id}")
        value = typer.prompt("选择 launch 序号或直接输入 launch id", default="1").strip()
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
