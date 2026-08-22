"""Interactive standalone and connected Market workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from prettytable import PrettyTable
import typer

from kairospy.application.launch.application import LaunchRegistryApplication

from ...models import GuidedCommand, InteractiveContext, ShellAction, ShellControl
from . import reference


_SNAPSHOT_KINDS = {
    "3": "quote",
    "quote": "quote",
    "4": "bar",
    "bar": "bar",
    "5": "greeks",
    "greeks": "greeks",
}

_DIRECT_PROVIDERS = {
    "spot": (
        ("binance-spot-rest", "Binance Spot REST"),
        ("binance-spot-websocket", "Binance Spot WebSocket"),
    ),
    "option": (("binance-options-rest", "Binance Options REST"),),
}


def print_menu(context: InteractiveContext) -> None:
    scope = _scope(context)
    if scope == "direct":
        typer.echo(
            "\n".join(
                (
                    "Market 独立模式（直接访问 provider，不连接 Market runtime）：",
                    "  1. 读取一次远端行情",
                    "  2. 验证 Market 描述",
                    "  3. 回放本地行情文件",
                    "  4. 下载历史行情",
                    "  5. 查看 Reference universe",
                    "  system market. 进入 workspace Market 连接模式",
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
                    "  2. 查看数据源",
                    "  3. Quote 快照",
                    "  4. K 线快照",
                    "  5. Greeks 快照",
                    "  6. 查看行情新鲜度",
                    "  7. 启动服务",
                    "  8. 停止服务",
                    "  9. 重启服务",
                    "  10. 查看日志",
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
                "  2. 查看数据源",
                "  3. Quote 快照",
                "  4. K 线快照",
                "  5. Greeks 快照",
                "  6. 查看行情新鲜度",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    if _scope(context) == "direct":
        typer.echo("可用命令：once/validate/replay/download/reference-universe/back/home/exit")
        typer.echo("这里直接调用 provider 或本地文件；运行中快照请进入 system/market。")
        return
    commands = "status/sources/quote/bar/greeks/freshness/back/home/exit"
    if _scope(context) == "system":
        commands = f"{commands}/start/stop/restart/logs"
    typer.echo(f"可用命令：{commands}")
    typer.echo("Market 和 Source 只能从当前作用域返回的列表中选择。")


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if _scope(context) == "direct":
        return _handle_direct(context, parts)
    if len(parts) != 1:
        return None
    key = parts[0]
    if key in {"1", "status"}:
        return _status_command(context)
    if key in {"2", "sources"}:
        return _sources_command(context)
    kind = _SNAPSHOT_KINDS.get(key)
    if kind is not None:
        return build_snapshot_command(context, kind)
    if key in {"6", "freshness"}:
        freshness_kind = _select_observation_kind()
        if freshness_kind is None:
            return ShellControl.HANDLED
        return build_snapshot_command(context, "freshness", freshness_kind)
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
        streaming=action == "logs",
    )


def enter_launch_market(context: InteractiveContext) -> ShellControl:
    """Select a registered launch instance before entering connected Market."""
    instance_id = _select_launch_instance(context)
    if instance_id is None:
        return ShellControl.HANDLED
    context.selected_launch_instance = instance_id
    launch_id = context.selected_launch or context.shell_path[1]
    context.shell_path = ("launch", launch_id, "market")
    context.selected_market = None
    context.selected_market_source = None
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
    source = _select_source(context, market, selected_kind)
    if source is None:
        return ShellControl.HANDLED
    market_id = str(market.id)
    source_id = str(source["source_id"])
    prefix = _command_prefix(context)
    if kind == "freshness":
        argv = (
            *prefix,
            "freshness",
            *_launch_argument(context),
            "--market-id",
            market_id,
            "--source-id",
            source_id,
            "--qualifier",
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
            "--source-id",
            source_id,
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
            "--source-id",
            source_id,
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
        raise typer.BadParameter("未完成 Market 或 Source 选择")
    return command


def _handle_direct(context: InteractiveContext, parts: tuple[str, ...]) -> ShellAction:
    if parts == ("system", "market"):
        context.shell_path = ("system", "market")
        context.selected_service = "market"
        context.selected_market = None
        return ShellControl.HANDLED
    if len(parts) != 1:
        return None
    action = {
        "1": "once",
        "once": "once",
        "2": "validate",
        "validate": "validate",
        "3": "replay",
        "replay": "replay",
        "4": "download",
        "download": "download",
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
    if action == "download":
        return _direct_download_command()
    if action == "reference-universe":
        return _direct_reference_universe_command()
    return None


def _direct_once_command(context: InteractiveContext) -> ShellAction:
    descriptor = _direct_descriptor(context)
    if descriptor is None:
        return ShellControl.HANDLED
    providers = _DIRECT_PROVIDERS.get(descriptor["market_type"], ())
    if not providers:
        typer.echo(
            f"独立 once 当前没有支持 {descriptor['market_type']} 的 direct provider。"
        )
        return ShellControl.HANDLED
    provider = providers[0][0]
    if len(providers) > 1:
        provider = _prompt_choice("选择 direct provider", providers)
        if provider is None:
            return ShellControl.HANDLED
    else:
        typer.echo(f"Direct provider：{providers[0][1]}")
    return GuidedCommand(
        (
            "market",
            "once",
            *_descriptor_arguments(descriptor),
            "--provider",
            provider,
            "--format",
            "table",
        ),
        f"直接从 {provider} 读取一次远端行情",
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
        "在独立模式验证 Market 描述",
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
        "在独立模式回放本地行情事件",
    )


def _direct_download_command() -> ShellAction:
    provider = _prompt_choice(
        "选择历史数据 provider", (("binance", "Binance"), ("massive", "Massive"))
    )
    market_type = _prompt_choice(
        "选择历史市场类型", (("equity", "股票"), ("option", "期权"))
    )
    data_kind = _prompt_choice(
        "选择历史数据类型", (("bar", "K 线"), ("quote", "报价"), ("trade", "成交"))
    )
    if provider is None or market_type is None or data_kind is None:
        return ShellControl.HANDLED
    symbol = typer.prompt("远端 symbol").strip()
    market_id = ""
    if provider == "binance":
        market_id = typer.prompt("Canonical Market ID").strip()
    instrument_id = typer.prompt("Canonical Instrument ID").strip()
    network_id = ""
    if provider == "massive":
        network_id = typer.prompt("Network ID（可留空）", default="").strip()
    start = typer.prompt("开始时间（Unix 毫秒）").strip()
    end = typer.prompt("结束时间（Unix 毫秒）").strip()
    destination = typer.prompt("保存文件").strip()
    if not all((symbol, instrument_id, start, end, destination)) or (
        provider == "binance" and not market_id
    ):
        typer.echo("symbol、canonical ID、开始时间、结束时间和保存文件均不能为空。")
        return ShellControl.HANDLED
    argv = (
        "market",
        "download",
        "--provider",
        provider,
        "--symbol",
        symbol,
        "--market-type",
        market_type,
        "--data-kind",
        data_kind,
        "--instrument-id",
        instrument_id,
        "--start",
        start,
        "--end",
        end,
        "--file",
        destination,
    )
    if market_id:
        argv = (*argv, "--market-id", market_id)
    if network_id:
        argv = (*argv, "--network-id", network_id)
    if data_kind == "bar":
        interval = typer.prompt("K 线周期", default="1m").strip()
        if not interval:
            return ShellControl.HANDLED
        argv = (*argv, "--interval", interval)
    return GuidedCommand((*argv, "--format", "table"), f"直接从 {provider} 下载历史行情")


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
        "读取独立模式 Reference Market universe",
    )


def _direct_descriptor(context: InteractiveContext) -> dict[str, str] | None:
    if context.owner is not None:
        mode = _prompt_choice(
            "选择 Market 描述方式",
            (("reference", "从 Reference 列表选择"), ("manual", "手动输入底层描述")),
        )
        if mode is None:
            return None
        if mode == "reference":
            record = reference.select_market(context)
            if record is None:
                return None
            symbol = record.venue_symbol or record.instrument.display_symbol
            if not symbol:
                typer.echo("所选 Reference Market 没有 provider symbol。")
                return None
            return {
                "market_id": str(record.id),
                "instrument_id": str(record.instrument.id),
                "exchange_id": str(record.exchange_id).rsplit(":", 1)[-1],
                "market_type": str(record.instrument_kind),
                "source_symbol": str(symbol),
            }
    typer.echo("手动输入仅属于 standalone/direct，不会访问运行中的 Market runtime。")
    values = {
        "market_id": typer.prompt("Market ID").strip(),
        "instrument_id": typer.prompt("Instrument ID").strip(),
        "exchange_id": typer.prompt("Exchange ID").strip(),
        "market_type": typer.prompt("Market type").strip(),
        "source_symbol": typer.prompt("Provider symbol").strip(),
    }
    if any(not value for value in values.values()):
        typer.echo("Market 底层描述字段均不能为空。")
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
        "--source-symbol",
        descriptor["source_symbol"],
    )


def _prompt_choice(
    title: str, choices: tuple[tuple[str, str], ...]
) -> str | None:
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


def _sources_command(context: InteractiveContext) -> ShellAction:
    market = reference.select_market(context)
    if market is None:
        return ShellControl.HANDLED
    return GuidedCommand(
        (
            *_command_prefix(context),
            "sources",
            *_launch_argument(context),
            "--market-id",
            str(market.id),
            "--configured-only",
            "--format",
            "table",
        ),
        "查看目标 Market runtime 的已配置数据源",
    )


def _select_source(
    context: InteractiveContext, market: Any, observation_kind: str
) -> dict[str, Any] | None:
    try:
        payload = _load_sources(context, str(market.id), observation_kind)
    except Exception as error:
        typer.echo(f"读取目标 Market 数据源失败：{error}")
        return None
    raw_sources = payload.get("sources", [])
    sources = [dict(value) for value in raw_sources if isinstance(value, Mapping)]
    if not sources:
        typer.echo(f"目标 Market 没有支持 {observation_kind} 的已配置数据源。")
        typer.echo(f"已查询：{market.id}")
        typer.echo("请查看当前作用域的数据源配置或订阅状态。")
        return None

    current = context.selected_market_source
    if current is not None and any(
        str(value.get("source_id")) == str(current.get("source_id"))
        for value in sources
    ):
        typer.echo(f"继续使用当前数据源：{current['source_id']}")
        return current

    table = PrettyTable(
        ["序号", "Source", "Provider", "配置", "状态", "Ready", "支持行情"]
    )
    table.align = "l"
    for index, source in enumerate(sources, 1):
        table.add_row(
            [
                index,
                source.get("source_id", "—"),
                source.get("provider_id") or "—",
                "是" if source.get("configured") else "否",
                source.get("status", "—"),
                "是" if source.get("ready") else "否",
                ", ".join(map(str, source.get("observation_capabilities", []))) or "—",
            ]
        )
    typer.echo(f"可用于 {observation_kind} 的数据源：")
    typer.echo(table)
    choice = typer.prompt("选择 Source 序号；输入 b 返回", default="b").strip()
    if choice in {"b", "back", ""}:
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(sources):
        typer.echo("无效的 Source 序号；不会尝试读取行情文件。")
        return None
    selected = sources[int(choice) - 1]
    context.selected_market_source = selected
    return selected


def _load_sources(
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
            context.owner, "sources", arguments
        )
    from kairospy.surface.cli.commands.launch import (
        _run_instance_market_connected_command,
    )

    return _run_instance_market_connected_command(
        context.owner,
        launch_id=context.selected_launch or context.shell_path[1],
        instance=_require_launch_instance(context),
        command="sources",
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
    if path == ("market",):
        return "direct"
    if path[:2] == ("system", "market"):
        return "system"
    if (
        len(path) >= 3
        and path[0] == "launch"
        and path[2] == "market"
    ):
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
