"""Interactive standalone and connected Market workflows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from prettytable import PrettyTable
import typer

from kairospy.application.launch.application import LaunchRegistryApplication
from kairospy.application.market.cli import MarketCliApplication

from ...models import (
    CommandExecution,
    GuidedCommand,
    InteractiveContext,
    ShellAction,
    ShellControl,
)
from . import reference


_SNAPSHOT_KINDS = {
    "3": "quote",
    "quote": "quote",
    "4": "bar",
    "bar": "bar",
    "5": "greeks",
    "greeks": "greeks",
}

_DIRECT_DATA_KINDS = {
    "equity": (
        ("quote", "最新报价"),
        ("trade", "最近成交"),
        ("bar", "最新分钟 K"),
    ),
    "spot": (
        ("quote", "最新报价"),
        ("trade", "最近成交"),
        ("bar", "最新 K 线"),
        ("order-book", "买卖盘口"),
    ),
    "option": (
        ("quote", "最新报价"),
        ("trade", "最近成交"),
        ("bar", "最新 K 线"),
        ("order-book", "买卖盘口"),
        ("option-greeks", "Greeks"),
    ),
}

_HISTORICAL_PROVIDERS = {
    "spot": (("binance", "Binance"),),
    "equity": (("massive", "Massive"),),
    "option": (("massive", "Massive"),),
}

_HISTORICAL_DATA_KINDS = {
    "binance": (("bar", "K 线"), ("quote", "报价"), ("trade", "成交")),
    "massive": (("bar", "K 线"), ("quote", "报价")),
}


def print_menu(context: InteractiveContext) -> None:
    scope = _scope(context)
    if scope == "direct":
        if context.shell_path == ("market", "diagnostics"):
            typer.echo(
                "\n".join(
                    (
                        "Market 诊断：",
                        "  1. 验证市场定义",
                        "  2. 检查 Reference → Market 映射",
                    )
                )
            )
            return
        if _has_direct_market_context(context):
            descriptor = _selected_direct_descriptor(context)
            if descriptor is None:
                _clear_direct_market_context(context)
            else:
                provider = context.selected_market_provider
                provider_label = (
                    str(provider.get("provider"))
                    if provider is not None
                    else "尚未选择"
                )
                lines = [
                    f"当前标的：{descriptor['symbol']} · "
                    f"{descriptor['exchange_id']} · {descriptor['market_type']}",
                    f"数据 Provider：{provider_label}",
                ]
                lines.extend(
                    f"  {index}. {label}"
                    for index, (_kind, label) in enumerate(
                        _DIRECT_DATA_KINDS.get(descriptor["market_type"], ()), 1
                    )
                )
                lines.append("  s. 切换标的")
                typer.echo("\n".join(lines))
                return
        typer.echo(
            "\n".join(
                (
                    "行情中心：",
                    "  1. 搜索标的并查看行情",
                    "  2. 下载历史行情",
                    "  3. 查看本地行情数据",
                    "  c. 连接运行中的行情服务",
                    "  d. 诊断问题",
                    "  a. 高级：输入完整市场标识",
                )
            )
        )
        return
    if scope == "system":
        typer.echo(
            "\n".join(
                (
                    "当前 Market：workspace 共享服务（连接模式）",
                    "  1. 查看状态",
                    "  2. 查看数据路由",
                    "  3. Quote 快照",
                    "  4. K 线快照",
                    "  5. Greeks 快照",
                    "  6. 查看行情新鲜度",
                    "  7. 启动服务",
                    "  8. 停止服务",
                    "  9. 重启服务",
                    "  10. 查看日志",
                    "  p. 暂停行情回放",
                    "  r. 继续行情回放",
                )
            )
        )
        return
    launch_id = context.selected_launch or context.shell_path[1]
    typer.echo(
        "\n".join(
            (
                f"当前 Market：launch={launch_id} "
                f"instance={context.selected_launch_instance or '—'}（连接模式）",
                "  1. 查看状态",
                "  2. 查看数据路由",
                "  3. Quote 快照",
                "  4. K 线快照",
                "  5. Greeks 快照",
                "  6. 查看行情新鲜度",
                "  p. 暂停行情回放",
                "  r. 继续行情回放",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    if _scope(context) == "direct":
        if context.shell_path == ("market", "diagnostics"):
            typer.echo("可用命令：validate/reference-universe/back/home/exit")
            typer.echo("这里只检查市场定义和 Reference 到 Market 的映射。")
            return
        if _has_direct_market_context(context):
            typer.echo("可用命令：quote/trade/bar/order-book/switch/back/home/exit")
            typer.echo("行情查询完成后会保留当前标的；输入 switch 切换标的。")
            return
        typer.echo(
            "可用命令：once/download/datasets/replay/connect/diagnostics/advanced/"
            "back/home/exit"
        )
        typer.echo("查看运行中服务的行情时，请选择“连接运行中的行情服务”。")
        return
    commands = (
        "status/routes/quote/bar/greeks/freshness/"
        "pause-replay/resume-replay/back/home/exit"
    )
    if _scope(context) == "system":
        commands = f"{commands}/start/stop/restart/logs"
    typer.echo(f"可用命令：{commands}")
    typer.echo("Market 和 Provider 只能从当前作用域返回的 route 列表中选择。")


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if _scope(context) == "direct":
        return _handle_direct(context, parts)
    if len(parts) != 1:
        return None
    key = parts[0]
    if key in {"1", "status"}:
        return _status_command(context)
    if key in {"2", "routes"}:
        return _routes_command(context)
    kind = _SNAPSHOT_KINDS.get(key)
    if kind is not None:
        return build_snapshot_command(context, kind)
    if key in {"6", "freshness"}:
        freshness_kind = _select_observation_kind()
        if freshness_kind is None:
            return ShellControl.HANDLED
        return build_snapshot_command(context, "freshness", freshness_kind)
    replay_action = {
        "p": "pause-replay",
        "pause-replay": "pause-replay",
        "pause": "pause-replay",
        "r": "resume-replay",
        "resume-replay": "resume-replay",
        "resume": "resume-replay",
    }.get(key)
    if replay_action is not None:
        return _replay_control_command(context, replay_action)
    if _scope(context) != "system":
        return None
    action = {
        "7": "up",
        "start": "up",
        "8": "down",
        "stop": "down",
        "9": "restart",
        "restart": "restart",
        "10": "logs",
        "logs": "logs",
    }.get(key)
    if action is None:
        return None
    argv = ("system", action, "--component", "market")
    if action != "logs":
        argv = (*argv, "--format", "text")
    return GuidedCommand(
        argv,
        {
            "up": "启动 workspace Market 服务",
            "down": "停止 workspace Market 服务",
            "restart": "重启 workspace Market 服务",
            "logs": "查看 workspace Market 日志",
        }[action],
        dangerous=action in {"up", "down", "restart"},
        execution=(
            CommandExecution.STREAMING
            if action == "logs"
            else CommandExecution.ACTIVITY
        ),
    )


def enter_launch_market(context: InteractiveContext) -> ShellControl:
    """Select a registered launch instance before entering connected Market."""
    instance_id = _select_launch_instance(context)
    if instance_id is None:
        return ShellControl.HANDLED
    context.selected_launch_instance = instance_id
    launch_id = context.selected_launch or context.shell_path[1]
    context.shell_path = (
        "launch",
        launch_id,
        "instances",
        instance_id,
        "components",
        "market",
    )
    context.selected_market = None
    context.selected_market_provider = None
    return ShellControl.HANDLED


def build_snapshot_command(
    context: InteractiveContext,
    kind: str,
    observation_kind: str | None = None,
) -> ShellAction:
    market = reference.select_market(context)
    if market is None:
        return ShellControl.HANDLED
    selected_kind = observation_kind or kind
    provider_route = _select_provider(context, market, selected_kind)
    if provider_route is None:
        return ShellControl.HANDLED
    market_id = str(market.id)
    provider = str(provider_route["provider"])
    prefix = _command_prefix(context)
    if kind == "freshness":
        argv = (
            *prefix,
            "freshness",
            *_launch_argument(context),
            "--market-id",
            market_id,
            "--provider",
            provider,
            "--observation",
            selected_kind,
            "--format",
            "table",
        )
        return GuidedCommand(argv, "查看连接模式 Market 行情新鲜度")

    if _scope(context) == "system":
        argv = (
            *prefix,
            "snapshot",
            kind,
            "--market-id",
            market_id,
            "--provider",
            provider,
        )
    else:
        argv = (
            *prefix,
            "snapshot",
            context.selected_launch or context.shell_path[1],
            kind,
            "--instance",
            _require_launch_instance(context),
            "--market-id",
            market_id,
            "--provider",
            provider,
        )
    if kind == "bar":
        timeframe = typer.prompt("timeframe", default="1m").strip()
        if not timeframe:
            typer.echo("timeframe 不能为空。")
            return ShellControl.HANDLED
        argv = (*argv, "--timeframe", timeframe)
    return GuidedCommand((*argv, "--format", "table"), f"读取当前 {kind} 行情快照")


def choose(context: InteractiveContext) -> GuidedCommand:
    """Build a Market command with an explicit direct or connected scope."""
    typer.echo(
        "选择 Market 模式：\n"
        "  1. 独立模式（直接访问 provider）\n"
        "  2. workspace system（连接模式）\n"
        "  3. launch instance（连接模式）"
    )
    scope = typer.prompt("请输入序号", default="1").strip()
    if scope == "1":
        context.shell_path = ("market",)
        command = _direct_once_command(context)
        if not isinstance(command, GuidedCommand):
            raise typer.BadParameter("未完成 direct Market 选择")
        return command
    if scope == "2":
        context.shell_path = ("system", "market")
        context.selected_service = "market"
    elif scope == "3":
        from ..strategy import launch

        launch_id = launch.prompt_launch_id(
            context.owner, context.snapshot, context.selected_launch
        )
        context.selected_launch = launch_id
        context.shell_path = ("launch", launch_id)
        if enter_launch_market(context) is not ShellControl.HANDLED:
            raise typer.BadParameter("无法选择 launch Market")
        if context.shell_path != ("launch", launch_id, "market"):
            raise typer.BadParameter("未选择 launch instance")
    else:
        raise typer.BadParameter("Market 模式必须从列表中选择")
    typer.echo("选择快照类型：\n  1. Quote\n  2. K 线\n  3. Greeks")
    kind = {"1": "quote", "2": "bar", "3": "greeks"}.get(
        typer.prompt("请输入序号", default="1").strip()
    )
    if kind is None:
        raise typer.BadParameter("快照类型必须从列表中选择")
    command = build_snapshot_command(context, kind)
    if not isinstance(command, GuidedCommand):
        raise typer.BadParameter("未完成 Market 或 Provider 选择")
    return command


def _handle_direct(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if context.shell_path == ("market", "diagnostics"):
        if len(parts) != 1:
            return None
        action = {
            "1": "validate",
            "validate": "validate",
            "2": "reference-universe",
            "reference-universe": "reference-universe",
            "universe": "reference-universe",
        }.get(parts[0])
        if action == "validate":
            return _direct_validate_command(context)
        if action == "reference-universe":
            return _direct_reference_universe_command()
        return None
    if parts in {("c",), ("connect",), ("system", "market")}:
        context.shell_path = ("system", "market")
        context.selected_service = "market"
        context.selected_market = None
        context.selected_market_provider = None
        return ShellControl.HANDLED
    if _has_direct_market_context(context):
        if len(parts) != 1:
            return None
        key = parts[0]
        if key in {"s", "switch"}:
            _clear_direct_market_context(context)
            return _direct_once_command(context)
        descriptor = _selected_direct_descriptor(context)
        if descriptor is None:
            _clear_direct_market_context(context)
            return ShellControl.HANDLED
        choices = _DIRECT_DATA_KINDS.get(descriptor["market_type"], ())
        selected_kind = next(
            (
                kind
                for index, (kind, _label) in enumerate(choices, 1)
                if key in {str(index), kind}
            ),
            None,
        )
        if selected_kind is not None:
            return _direct_once_command(context, observation_kind=selected_kind)
        return None
    if parts in {("d",), ("diagnostics",)}:
        context.shell_path = ("market", "diagnostics")
        return ShellControl.HANDLED
    if parts in {("a",), ("advanced",)}:
        return _direct_once_command(context, manual=True)
    if len(parts) != 1:
        return None
    action = {
        "1": "once",
        "once": "once",
        "2": "download",
        "download": "download",
        "3": "datasets",
        "datasets": "datasets",
        "local": "datasets",
        "replay": "replay",
        "4": "validate",
        "validate": "validate",
        "5": "reference-universe",
        "reference-universe": "reference-universe",
        "universe": "reference-universe",
    }.get(parts[0])
    if action == "once":
        return _direct_once_command(context)
    if action == "validate":
        return _direct_validate_command(context)
    if action == "replay":
        return _direct_replay_command(context)
    if action == "datasets":
        return GuidedCommand(
            ("market", "datasets", "--format", "table"),
            "查看本地行情数据",
        )
    if action == "download":
        return _direct_download_command(context)
    if action == "reference-universe":
        return _direct_reference_universe_command()
    return None


def _direct_once_command(
    context: InteractiveContext,
    *,
    manual: bool = False,
    observation_kind: str | None = None,
) -> ShellAction:
    descriptor = (
        _manual_descriptor()
        if manual
        else _direct_descriptor(
            context,
            availability_label="实时行情",
        )
    )
    if descriptor is None:
        return ShellControl.HANDLED
    if manual:
        context.selected_market = descriptor
    context.shell_path = ("market", descriptor["symbol"])
    data_kinds = _DIRECT_DATA_KINDS.get(descriptor["market_type"], ())
    if not data_kinds:
        typer.echo("已找到该标的，但当前没有可用的实时 Provider route。")
        if descriptor["market_type"] in _HISTORICAL_PROVIDERS:
            typer.echo("可返回行情中心选择“下载历史行情”。")
        return ShellControl.HANDLED
    if observation_kind is None:
        selected_kind = _prompt_data_kind(data_kinds)
        if selected_kind is None:
            return ShellControl.HANDLED
        observation_kind, kind_label = selected_kind
    else:
        kind_label = next(
            (label for kind, label in data_kinds if kind == observation_kind),
            observation_kind,
        )
    provider_route = _select_direct_provider(context, descriptor, observation_kind)
    if provider_route is None:
        return ShellControl.HANDLED
    return GuidedCommand(
        (
            "market",
            "once",
            *_descriptor_arguments(descriptor),
            "--provider",
            str(provider_route["provider"]),
            "--observation-kind",
            observation_kind,
            "--format",
            "table",
        ),
        f"查看 {descriptor['symbol']} {kind_label}",
        show_command=False,
    )


def _prompt_data_kind(choices: tuple[tuple[str, str], ...]) -> tuple[str, str] | None:
    typer.echo("你想查看：")
    for index, (_kind, label) in enumerate(choices, 1):
        typer.echo(f"  {index}. {label}")
    choice = typer.prompt("请输入序号；输入 b 返回", default="1").strip()
    if choice in {"b", "back"}:
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(choices):
        typer.echo("无效的选项序号。")
        return None
    return choices[int(choice) - 1]


def _select_direct_provider(
    context: InteractiveContext,
    descriptor: Mapping[str, str],
    observation_kind: str,
) -> dict[str, Any] | None:
    try:
        payload = _load_direct_routes(
            context,
            descriptor["market_type"],
            observation_kind,
        )
    except Exception as error:
        typer.echo(f"读取可用 Provider route 失败：{error}")
        return None
    raw_routes = payload.get("routes", [])
    routes = [dict(value) for value in raw_routes if isinstance(value, Mapping)]
    if not routes:
        typer.echo("当前没有支持这种行情的已配置 Provider route。")
        return None
    current = context.selected_market_provider
    if current is not None:
        selected = next(
            (
                route
                for route in routes
                if str(route.get("provider")) == str(current.get("provider"))
            ),
            None,
        )
        if selected is not None:
            typer.echo(f"继续使用当前 Provider：{selected.get('provider')}")
            context.selected_market_provider = selected
            return selected
    if len(routes) == 1:
        context.selected_market_provider = routes[0]
        return routes[0]
    typer.echo("可用 Provider：")
    for index, route in enumerate(routes, 1):
        typer.echo(f"  {index}. {route['provider']}")
    choice = typer.prompt("选择 Provider 序号；输入 b 返回", default="1").strip()
    if choice in {"b", "back"}:
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(routes):
        typer.echo("无效的 Provider 序号。")
        return None
    selected = routes[int(choice) - 1]
    context.selected_market_provider = selected
    return selected


def _load_direct_routes(
    context: InteractiveContext,
    market_type: str,
    observation_kind: str,
) -> dict[str, Any]:
    return MarketCliApplication(context.owner).run(
        (
            "standalone",
            "routes",
            "--market-type",
            market_type,
            "--observation-kind",
            observation_kind,
        )
    )


def _direct_validate_command(context: InteractiveContext) -> ShellAction:
    descriptor = _direct_descriptor(context)
    if descriptor is None:
        return ShellControl.HANDLED
    return GuidedCommand(
        (
            "market",
            "validate",
            *_descriptor_arguments(descriptor),
            "--format",
            "table",
        ),
        "验证市场定义",
    )


def _direct_replay_command(context: InteractiveContext) -> ShellAction:
    descriptor = _direct_descriptor(context)
    if descriptor is None:
        return ShellControl.HANDLED
    path = typer.prompt("行情事件文件").strip()
    if not path:
        typer.echo("行情事件文件不能为空。")
        return ShellControl.HANDLED
    return GuidedCommand(
        (
            "market",
            "replay",
            *_descriptor_arguments(descriptor),
            "--file",
            path,
            "--format",
            "table",
        ),
        "回放本地行情数据文件",
    )


def _direct_download_command(context: InteractiveContext) -> ShellAction:
    descriptor = _direct_descriptor(
        context,
        allowed_market_types=tuple(_HISTORICAL_PROVIDERS),
        availability_label="历史行情",
    )
    if descriptor is None:
        return ShellControl.HANDLED
    providers = _HISTORICAL_PROVIDERS[descriptor["market_type"]]
    if len(providers) == 1:
        provider = providers[0][0]
        typer.echo(f"历史行情 Provider：{providers[0][1]}")
    else:
        provider = _prompt_choice("选择历史行情 Provider", providers)
        if provider is None:
            return ShellControl.HANDLED
    data_kind = _prompt_choice("选择要下载的行情", _HISTORICAL_DATA_KINDS[provider])
    if data_kind is None:
        return ShellControl.HANDLED
    today = datetime.now(timezone.utc).date()
    start_text = typer.prompt(
        "开始日期", default=(today - timedelta(days=30)).isoformat()
    ).strip()
    end_text = typer.prompt("结束日期", default=today.isoformat()).strip()
    try:
        start = _history_time_millis(start_text, end_of_day=False)
        end = _history_time_millis(end_text, end_of_day=True)
    except ValueError as error:
        typer.echo(f"日期格式无效：{error}")
        return ShellControl.HANDLED
    if start >= end:
        typer.echo("开始日期必须早于结束日期。")
        return ShellControl.HANDLED
    default_file = (
        f"market-history/{_safe_filename(descriptor['symbol'])}-{data_kind}.jsonl"
    )
    destination = typer.prompt("保存位置", default=default_file).strip()
    if not destination:
        typer.echo("保存位置不能为空。")
        return ShellControl.HANDLED
    argv = (
        "market",
        "download",
        "--provider",
        provider,
        "--symbol",
        descriptor["symbol"],
        "--market-type",
        descriptor["market_type"],
        "--data-kind",
        data_kind,
        "--instrument-id",
        descriptor["instrument_id"],
        "--start",
        str(start),
        "--end",
        str(end),
        "--file",
        destination,
    )
    if provider == "binance":
        argv = (*argv, "--market-id", descriptor["market_id"])
    if data_kind == "bar":
        interval = typer.prompt("K 线周期", default="1d").strip()
        if not interval:
            return ShellControl.HANDLED
        argv = (*argv, "--interval", interval)
    return GuidedCommand(
        (*argv, "--format", "table"),
        f"下载 {descriptor['symbol']} 历史行情",
    )


def _history_time_millis(value: str, *, end_of_day: bool) -> int:
    normalized = value.strip()
    if normalized.isdigit():
        return int(normalized)
    try:
        parsed_date = date.fromisoformat(normalized)
    except ValueError:
        try:
            parsed_datetime = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("请输入 YYYY-MM-DD、ISO 8601 时间或 Unix 毫秒") from error
        if parsed_datetime.tzinfo is None:
            parsed_datetime = parsed_datetime.replace(tzinfo=timezone.utc)
        return int(parsed_datetime.timestamp() * 1000)
    boundary = time.max if end_of_day else time.min
    parsed_datetime = datetime.combine(parsed_date, boundary, tzinfo=timezone.utc)
    return int(parsed_datetime.timestamp() * 1000)


def _safe_filename(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in value
    ).strip("-")
    return cleaned or "market"


def _direct_reference_universe_command() -> ShellAction:
    kind = _prompt_choice(
        "选择 instrument 类型",
        (
            ("equity", "股票"),
            ("spot", "现货"),
            ("perpetual", "永续"),
            ("future", "交割期货"),
            ("option", "期权"),
            ("index", "指数"),
        ),
    )
    if kind is None:
        return ShellControl.HANDLED
    limit = typer.prompt("最大条数", default="10000").strip()
    if not limit.isdigit() or int(limit) <= 0:
        typer.echo("最大条数必须是正整数。")
        return ShellControl.HANDLED
    return GuidedCommand(
        (
            "market",
            "reference-universe",
            "--instrument-kind",
            kind,
            "--limit",
            limit,
            "--format",
            "table",
        ),
        "检查 Reference → Market 映射",
    )


def _replay_control_command(context: InteractiveContext, action: str) -> GuidedCommand:
    label = "暂停" if action == "pause-replay" else "继续"
    return GuidedCommand(
        (
            *_command_prefix(context),
            action,
            *_launch_argument(context),
            "--format",
            "json",
        ),
        f"{label}当前 Market 服务的行情回放",
    )


def _direct_descriptor(
    context: InteractiveContext,
    *,
    allowed_market_types: tuple[str, ...] = (),
    availability_label: str = "行情查询",
) -> dict[str, str] | None:
    selected = _selected_direct_descriptor(context)
    if selected is not None:
        if not allowed_market_types or selected["market_type"] in allowed_market_types:
            return selected
        _clear_direct_market_context(context)
    if context.owner is not None:
        record = reference.select_market(
            context,
            allowed_instrument_kinds=allowed_market_types or None,
            availability_label=availability_label,
        )
        if record is None:
            return None
        symbol = record.venue_symbol or record.instrument.display_symbol
        if not symbol:
            typer.echo("所选标的缺少行情 Provider 使用的 symbol。")
            return None
        context.selected_market = record
        return {
            "market_id": str(record.id),
            "instrument_id": str(record.instrument.id),
            "exchange_id": str(record.exchange_id).rsplit(":", 1)[-1],
            "market_type": str(record.instrument_kind),
            "symbol": str(symbol),
        }
    typer.echo("当前没有可用的标的目录，将进入高级输入。")
    descriptor = _manual_descriptor()
    if descriptor is not None:
        context.selected_market = descriptor
    return descriptor


def _has_direct_market_context(context: InteractiveContext) -> bool:
    return (
        len(context.shell_path) == 2
        and context.shell_path[0] == "market"
        and context.shell_path[1] != "diagnostics"
        and context.selected_market is not None
    )


def _selected_direct_descriptor(
    context: InteractiveContext,
) -> dict[str, str] | None:
    selected = context.selected_market
    if selected is None:
        return None
    if isinstance(selected, Mapping):
        required = {
            "market_id",
            "instrument_id",
            "exchange_id",
            "market_type",
            "symbol",
        }
        if required.issubset(selected):
            return {key: str(selected[key]) for key in required}
        return None
    symbol = selected.venue_symbol or selected.instrument.display_symbol
    if not symbol:
        return None
    return {
        "market_id": str(selected.id),
        "instrument_id": str(selected.instrument.id),
        "exchange_id": str(selected.exchange_id).rsplit(":", 1)[-1],
        "market_type": str(selected.instrument_kind),
        "symbol": str(symbol),
    }


def _clear_direct_market_context(context: InteractiveContext) -> None:
    context.selected_market = None
    context.selected_market_provider = None
    context.shell_path = ("market",)


def _manual_descriptor() -> dict[str, str] | None:
    typer.echo("高级模式：输入完整市场标识（仅用于本次直接查询）。")
    values = {
        "market_id": typer.prompt("Market ID").strip(),
        "instrument_id": typer.prompt("Instrument ID").strip(),
        "exchange_id": typer.prompt("Exchange ID").strip(),
        "market_type": typer.prompt("Market type").strip(),
        "symbol": typer.prompt("Provider symbol").strip(),
    }
    if any(not value for value in values.values()):
        typer.echo("完整市场标识的所有字段都不能为空。")
        return None
    return values


def _descriptor_arguments(descriptor: Mapping[str, str]) -> tuple[str, ...]:
    return (
        "--market-id",
        descriptor["market_id"],
        "--instrument-id",
        descriptor["instrument_id"],
        "--exchange-id",
        descriptor["exchange_id"],
        "--market-type",
        descriptor["market_type"],
        "--symbol",
        descriptor["symbol"],
    )


def _prompt_choice(title: str, choices: tuple[tuple[str, str], ...]) -> str | None:
    typer.echo(title)
    for index, (_value, label) in enumerate(choices, 1):
        typer.echo(f"  {index}. {label}")
    choice = typer.prompt("请输入序号；输入 b 返回", default="1").strip()
    if choice in {"b", "back"}:
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(choices):
        typer.echo("无效的选项序号。")
        return None
    return choices[int(choice) - 1][0]


def _status_command(context: InteractiveContext) -> GuidedCommand:
    if _scope(context) == "system":
        return GuidedCommand(
            ("system", "component", "market", "status", "--format", "table"),
            "查看 workspace Market 服务状态",
        )
    launch_id = context.selected_launch or context.shell_path[1]
    return GuidedCommand(
        (
            *_command_prefix(context),
            "status",
            launch_id,
            "--instance",
            _require_launch_instance(context),
            "--format",
            "table",
        ),
        "查看 launch instance Market 状态",
    )


def _routes_command(context: InteractiveContext) -> ShellAction:
    market = reference.select_market(context)
    if market is None:
        return ShellControl.HANDLED
    return GuidedCommand(
        (
            *_command_prefix(context),
            "routes",
            *_launch_argument(context),
            "--market-id",
            str(market.id),
            "--configured-only",
            "--format",
            "table",
        ),
        "查看目标 Market 的已配置 Provider routes",
    )


def _select_provider(
    context: InteractiveContext, market: Any, observation_kind: str
) -> dict[str, Any] | None:
    try:
        payload = _load_routes(context, str(market.id), observation_kind)
    except Exception as error:
        typer.echo(f"读取目标 Market routes 失败：{error}")
        return None
    raw_routes = payload.get("routes", [])
    routes = [dict(value) for value in raw_routes if isinstance(value, Mapping)]
    if not routes:
        typer.echo(f"目标 Market 没有支持 {observation_kind} 的已配置 route。")
        typer.echo(f"已查询：{market.id}")
        typer.echo("请查看当前作用域的 Provider 配置或订阅状态。")
        return None

    current = context.selected_market_provider
    if current is not None and any(
        str(value.get("provider")) == str(current.get("provider")) for value in routes
    ):
        typer.echo(f"继续使用当前 Provider：{current['provider']}")
        return current

    table = PrettyTable(
        ["序号", "Provider", "状态", "Selected", "支持行情", "等待原因"]
    )
    table.align = "l"
    for index, route in enumerate(routes, 1):
        table.add_row(
            [
                index,
                route.get("provider", "—"),
                route.get("state", "—"),
                "是" if route.get("selected") else "否",
                ", ".join(map(str, route.get("observation_kinds", []))) or "—",
                route.get("pending_reason") or "—",
            ]
        )
    typer.echo(f"可用于 {observation_kind} 的 Provider routes：")
    typer.echo(table)
    choice = typer.prompt("选择 Provider 序号；输入 b 返回", default="b").strip()
    if choice in {"b", "back", ""}:
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(routes):
        typer.echo("无效的 Provider 序号；不会尝试读取行情文件。")
        return None
    selected = routes[int(choice) - 1]
    context.selected_market_provider = selected
    return selected


def _load_routes(
    context: InteractiveContext, market_id: str, observation_kind: str
) -> dict[str, Any]:
    if context.owner is None:
        raise RuntimeError("当前没有可用的 workspace")
    arguments = [
        "--market-id",
        market_id,
        "--observation-kind",
        observation_kind,
        "--configured-only",
    ]
    if _scope(context) == "system":
        from kairospy.surface.cli.commands.root import (
            _run_workspace_market_connected_command,
        )

        return _run_workspace_market_connected_command(
            context.owner, "routes", arguments
        )
    from kairospy.surface.cli.commands.launch import (
        _run_instance_market_connected_command,
    )

    return _run_instance_market_connected_command(
        context.owner,
        launch_id=context.selected_launch or context.shell_path[1],
        instance=_require_launch_instance(context),
        command="routes",
        arguments=arguments,
        require_views=False,
    )


def _select_observation_kind() -> str | None:
    typer.echo("选择要检查的新鲜度类型：\n  1. Quote\n  2. K 线\n  3. Greeks")
    value = typer.prompt("请输入序号；输入 b 返回", default="1").strip()
    if value in {"b", "back"}:
        return None
    kind = {"1": "quote", "2": "bar", "3": "greeks"}.get(value)
    if kind is None:
        typer.echo("无效的行情类型序号。")
    return kind


def _select_launch_instance(context: InteractiveContext) -> str | None:
    if context.owner is None:
        typer.echo("当前没有可用的 workspace。")
        return None
    launch_id = context.selected_launch or context.shell_path[1]
    records = list(LaunchRegistryApplication(context.owner).instances(launch_id))
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        instance_id = str(record.get("instance_id") or "").strip()
        mode = str(record.get("mode") or "paper")
        key = (instance_id, mode)
        if instance_id and key not in seen:
            seen.add(key)
            unique.append(record)
    if not unique:
        typer.echo(f"launch {launch_id} 当前没有已登记的 instance。")
        return None
    selected = context.selected_launch_instance
    if selected is not None and any(
        str(record.get("instance_id")) == selected for record in unique
    ):
        return selected
    if len(unique) == 1:
        selected = str(unique[0]["instance_id"])
        typer.echo(f"已选择唯一 instance：{selected}")
        context.selected_launch_instance = selected
        return selected
    table = PrettyTable(["序号", "Instance", "Mode", "State"])
    table.align = "l"
    for index, record in enumerate(unique, 1):
        table.add_row(
            [
                index,
                record.get("instance_id", "—"),
                record.get("mode", "—"),
                record.get("state", "—"),
            ]
        )
    typer.echo(table)
    choice = typer.prompt("选择 instance 序号；输入 b 返回", default="b").strip()
    if choice in {"b", "back", ""}:
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(unique):
        typer.echo("无效的 instance 序号。")
        return None
    selected = str(unique[int(choice) - 1]["instance_id"])
    context.selected_launch_instance = selected
    return selected


def _scope(context: InteractiveContext) -> str:
    path = context.shell_path
    if path[:1] == ("market",):
        return "direct"
    if path[:2] == ("system", "market"):
        return "system"
    if len(path) >= 6 and path[0] == "launch" and path[-2:] == ("components", "market"):
        return "launch"
    raise RuntimeError("Market 交互必须从 system 或 launch 连接作用域进入")


def _command_prefix(context: InteractiveContext) -> tuple[str, ...]:
    if _scope(context) == "system":
        return ("system", "component", "market")
    return ("launch", "instance", "component", "market")


def _launch_argument(context: InteractiveContext) -> tuple[str, ...]:
    if _scope(context) == "system":
        return ()
    path = context.shell_path
    if len(path) < 2:
        raise RuntimeError("Launch Market 交互缺少 launch 上下文")
    return (
        context.selected_launch or path[1],
        "--instance",
        _require_launch_instance(context),
    )


def _require_launch_instance(context: InteractiveContext) -> str:
    if context.selected_launch_instance is None:
        raise RuntimeError("请先从列表选择 launch instance")
    return context.selected_launch_instance
