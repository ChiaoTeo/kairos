"""Interactive Reference catalog section."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from prettytable import PrettyTable
import typer

from ...models import GuidedCommand, InteractiveContext, ShellAction, ShellControl


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo("s/search 搜索；l/list 浏览；也可直接输入代码或名称；back 返回。")


def print_summary(context: InteractiveContext) -> None:
    _print_reference_detail(context, technical=False)


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
            context.shell_path = ("reference", route)
            print_menu(context)
            keep_path = _reference_search_and_select(
                context, ("entity", label, entity_type), query=None
            )
            if not keep_path:
                context.shell_path = ("reference",)
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
    if query in {"search", "find", "s"}:
        query = typer.prompt("输入代码或名称").strip()
    elif query in {"list", "ls", "l"}:
        query = ""
    if not query and parts[0] not in {"list", "ls", "l", "refresh"}:
        typer.echo("请输入代码或名称；输入 list 可浏览前 10 条。")
        return ShellControl.HANDLED
    keep_path = _reference_search_and_select(context, collection, query or None)
    if collection[0] == "entity" and not keep_path:
        context.shell_path = ("reference",)
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


def print_menu(context: InteractiveContext) -> None:
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
                    "  选择类型后可搜索或浏览",
                )
            )
        )
        return
    collection = _reference_collection(path)
    if collection is not None:
        if collection[0] == "entity":
            typer.echo(f"{collection[1]}：输入 refresh 重新读取列表。")
        else:
            typer.echo(
                "\n".join(
                    (
                        f"{collection[1]}：",
                        "  s. 搜索代码或名称",
                        "  l. 浏览前 10 条",
                        "  也可以直接输入代码或名称",
                    )
                )
            )


def _reference_collection(path: tuple[str, ...]) -> tuple[str, str, str | None] | None:
    if path == ("reference", "assets"):
        return ("asset", "资产", None)
    if path == ("reference", "markets"):
        return ("market", "具体市场", None)
    if len(path) == 2 and path[0] == "reference":
        participant = _REFERENCE_PARTICIPANT_TYPES.get(path[1])
        if participant is not None:
            return ("entity", participant[1], participant[0])
    if len(path) == 3 and path[:2] == ("reference", "instruments"):
        instrument = _REFERENCE_INSTRUMENT_TYPES.get(path[2])
        if instrument is not None:
            return ("instrument", instrument[1], instrument[0])
    return None


def _application(context: InteractiveContext):
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
) -> bool:
    kind, label, subtype = collection
    try:
        app = _application(context)
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
        return True

    ranked = _rank_reference_records(kind, tuple(records), query)[:10]
    if not ranked:
        suffix = f"“{query}”" if query else "当前分类"
        typer.echo(f"没有找到与{suffix}匹配的{label}。")
        return True
    _render_reference_results(kind, ranked)
    choice = typer.prompt("输入序号查看详情；输入 b 返回", default="b").strip()
    if choice in {"b", "back", ""}:
        return False
    if not choice.isdigit() or not 1 <= int(choice) <= len(ranked):
        typer.echo("无效的结果序号。")
        return True
    selected = ranked[int(choice) - 1]
    context.selected_reference = selected
    context.selected_reference_kind = kind
    context.shell_path = (*context.shell_path, _reference_record_slug(kind, selected))
    _print_reference_detail(context, technical=False)
    return True


def select_market(
    context: InteractiveContext,
    *,
    allowed_instrument_kinds: Sequence[str] | None = None,
    availability_label: str = "行情查询",
) -> Any | None:
    """Select one canonical Market, optionally limited to currently usable kinds."""
    allowed_kinds = tuple(dict.fromkeys(allowed_instrument_kinds or ()))
    if context.selected_market is not None:
        record = context.selected_market
        if not allowed_kinds or str(record.instrument_kind) in allowed_kinds:
            typer.echo(
                "继续使用当前标的："
                f"{record.venue_symbol or record.instrument.display_symbol} · "
                f"{_short_id(record.exchange_id)}"
            )
            return record
        context.selected_market = None
        context.selected_market_source = None

    query = typer.prompt("输入代码或名称（直接回车浏览可用标的）", default="").strip()
    try:
        app = _application(context)
        if allowed_kinds:
            records = tuple(
                record
                for instrument_kind in allowed_kinds
                for record in app.find_markets(
                    query=query or None,
                    instrument_kind=instrument_kind,
                    active_only=True,
                    limit=25,
                )
            )
        else:
            records = app.find_markets(
                query=query or None, active_only=True, limit=25
            )
    except Exception as error:
        typer.echo(f"读取标的目录失败：{error}")
        return None
    unique_records = {str(record.id): record for record in records}
    ranked = _rank_reference_records(
        "market", tuple(unique_records.values()), query or None
    )[:10]
    if not ranked:
        if allowed_kinds:
            labels = "、".join(_instrument_type_label(kind) for kind in allowed_kinds)
            typer.echo(
                f"没有找到匹配且可用于{availability_label}的标的"
                f"（当前支持：{labels}）。"
            )
        else:
            typer.echo("标的目录中没有匹配的有效标的。")
        return None
    _render_reference_results("market", ranked)
    choice = typer.prompt("选择标的序号；输入 b 返回", default="b").strip()
    if choice in {"b", "back", ""}:
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(ranked):
        typer.echo("无效的标的序号；不会发起行情查询。")
        return None
    selected = ranked[int(choice) - 1]
    context.selected_market = selected
    context.selected_market_source = None
    return selected


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
    if record is None:
        typer.echo("请先选择一个 Reference 目录对象。")
        return ShellControl.HANDLED
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
        app = _application(context)
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


def choose(context: InteractiveContext) -> GuidedCommand:
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


def choose_option_chain() -> GuidedCommand:
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
