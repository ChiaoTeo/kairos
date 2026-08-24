"""Single-input Reference catalog helpers."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

from rich.console import RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table

from kairospy.investment.apps.reference.application import ReferenceApplication

from ...widgets import ActionItem


INSTRUMENT_TYPE_ACTIONS = (
    ActionItem("equity", "股票", "股票和 ETF 等权益类合约", "1"),
    ActionItem("spot", "现货", "数字资产等现货交易对", "2"),
    ActionItem("perpetual", "永续合约", "没有到期日的衍生品", "3"),
    ActionItem("future", "交割合约", "具有到期日的期货合约", "4"),
    ActionItem("option", "期权", "看涨与看跌期权合约", "5"),
    ActionItem("index", "指数", "市场指数与基准", "6"),
)


def detail_actions(kind: str | None) -> tuple[ActionItem, ...]:
    if kind == "assets":
        return (
            ActionItem("summary", "概览", "查看资产名称、类型与状态", "1"),
            ActionItem("markets", "相关市场", "查找使用该资产的具体市场", "2"),
            ActionItem("technical", "技术标识", "显示完整 Asset ID", "3"),
        )
    if kind in {"instruments", "option-chain"}:
        return (
            ActionItem("summary", "概览", "查看合约类型、状态和到期信息", "1"),
            ActionItem("listings", "上市信息", "查看交易所上市记录", "2"),
            ActionItem("markets", "具体市场", "查看该合约对应的市场", "3"),
            ActionItem("technical", "技术标识", "显示完整 Instrument ID", "4"),
        )
    if kind == "exchanges":
        return (
            ActionItem("summary", "概览", "查看交易所名称与状态", "1"),
            ActionItem("related", "上市信息或市场", "查看交易所上市记录", "2"),
            ActionItem("technical", "技术标识", "显示完整 Exchange ID", "3"),
        )
    if kind == "markets":
        return (
            ActionItem("summary", "概览", "查看市场、交易所和计价资产", "1"),
            ActionItem("technical", "技术标识", "显示完整 Market 与合约 ID", "2"),
        )
    return ()


def load_records(
    state: Any,
    kind: str,
    query: str,
    *,
    instrument_type: str | None = None,
) -> tuple[Any, ...]:
    application = _application(state)
    if kind == "assets":
        records = application.find_assets(
            query=query or None, active_only=True, limit=25
        )
    elif kind == "exchanges":
        records = application.find_exchanges(
            query=query or None, active_only=True, limit=25
        )
    elif kind == "instruments":
        records = application.find_instruments(
            query=query or None,
            instrument_type=instrument_type,
            active_only=True,
            limit=25,
        )
    elif kind == "markets":
        records = application.find_markets(
            query=query or None, active_only=True, limit=25
        )
    elif kind == "option-chain":
        if not query:
            raise ValueError("请输入标的合约 ID。")
        records = application.option_chain(query, active_only=True, limit=100)
    else:
        raise RuntimeError(f"unknown reference kind: {kind}")
    return rank_records(record_kind(kind), tuple(records), query or None)[:25]


def load_related(state: Any, kind: str, record: Any) -> tuple[str, tuple[Any, ...]]:
    application = _application(state)
    if kind == "assets":
        return (
            "markets",
            application.find_markets(
                asset_code=str(record.code), active_only=True, limit=10
            ),
        )
    if kind in {"instruments", "option-chain"}:
        return (
            "listings",
            application.find_listings(
                instrument_id=record.id, active_only=True, limit=10
            ),
        )
    if kind == "exchanges":
        return (
            "listings",
            application.find_listings(exchange=record.id, active_only=True, limit=10),
        )
    raise ValueError("当前记录没有该关联查询。")


def load_instrument_markets(state: Any, record: Any) -> tuple[str, tuple[Any, ...]]:
    return (
        "markets",
        _application(state).find_markets(
            instrument_id=record.id, active_only=True, limit=10
        ),
    )


def detail_renderable(
    record: Any, kind: str | None, *, technical: bool = False
) -> RenderableType:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    if kind == "assets":
        rows = (
            ("代码", record.code),
            ("名称", record.name or "—"),
            ("资产类型", record.asset_class),
            ("状态", record.status),
        )
        technical_rows = (("Asset ID", record.id),)
    elif kind == "exchanges":
        rows = (("名称", record.name), ("状态", record.status))
        technical_rows = (("Exchange ID", record.id),)
    elif kind in {"instruments", "option-chain"}:
        rows = (
            ("代码", record.symbol),
            ("名称", record.name or "—"),
            ("品种类型", record.instrument_type),
            ("状态", record.status),
            ("到期时间", record.expiry_unix_nanos or "—"),
            ("行权价", record.strike or "—"),
            ("期权方向", record.option_right or "—"),
        )
        technical_rows = (
            ("Instrument ID", record.id),
            ("Underlying ID", record.underlying_instrument_id or "—"),
        )
    elif kind == "markets":
        rows = (
            ("代码", record.venue_symbol or record.instrument.display_symbol),
            ("交易所", _short_id(record.exchange_id)),
            ("市场类型", record.instrument_kind),
            ("基础资产", _short_id(record.base_asset)),
            ("计价资产", _short_id(record.quote_asset)),
            ("状态", record.status),
        )
        technical_rows = (
            ("Market ID", record.id),
            ("Instrument ID", record.instrument.id),
            ("Listing ID", record.listing_id or "—"),
        )
    else:
        return Panel(Pretty(_as_value(record), expand_all=True), title="Reference")
    for label, value in (*rows, *(technical_rows if technical else ())):
        table.add_row(label, str(value))
    return Panel(
        table,
        title=f"{record_label(record)} · {'技术标识' if technical else '概览'}",
        border_style="cyan",
    )


def records_renderable(kind: str, records: tuple[Any, ...]) -> RenderableType:
    titles = {
        "assets": "资产",
        "exchanges": "交易所",
        "instruments": "合约",
        "markets": "交易标的",
        "option-chain": "期权链",
        "listings": "上市信息",
    }
    if not records:
        return Panel("没有找到匹配的记录。", title=titles.get(kind, "Reference"))
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="bold cyan")
    table.add_column("名称")
    table.add_column("说明")
    table.add_column("ID", style="dim")
    for index, record in enumerate(records, 1):
        table.add_row(
            str(index), record_label(record), record_description(record), str(record.id)
        )
    return Panel(
        table,
        title=f"找到 {len(records)} 条{titles.get(kind, 'Reference')}记录",
        border_style="cyan",
    )


def record_kind(kind: str) -> str:
    if kind == "assets":
        return "asset"
    if kind == "exchanges":
        return "exchange"
    if kind in {"instruments", "option-chain"}:
        return "instrument"
    return "market"


def rank_records(
    kind: str, records: tuple[Any, ...], query: str | None
) -> tuple[Any, ...]:
    if not query:
        return records
    expected = query.casefold()

    def rank(record: Any) -> tuple[int, str]:
        values = _search_values(kind, record)
        lowered = tuple(value.casefold() for value in values if value)
        if expected in lowered:
            score = 0
        elif any(value.startswith(expected) for value in lowered):
            score = 1
        else:
            score = 2
        return score, lowered[0] if lowered else ""

    return tuple(sorted(records, key=rank))


def record_label(record: Any) -> str:
    for name in ("venue_symbol", "exchange_symbol", "symbol", "code", "name"):
        value = getattr(record, name, None)
        if value:
            return str(value)
    instrument = getattr(record, "instrument", None)
    return str(getattr(instrument, "display_symbol", None) or record.id)


def record_description(record: Any) -> str:
    values: list[str] = []
    for name in (
        "name",
        "instrument_kind",
        "instrument_type",
        "asset_class",
        "status",
    ):
        value = getattr(record, name, None)
        if value and str(value) not in values:
            values.append(str(value))
    return " · ".join(values) or str(record.id)


def _application(state: Any) -> ReferenceApplication:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    return ReferenceApplication.from_database(state.owner.paths.reference_database())


def _search_values(kind: str, record: Any) -> tuple[str, ...]:
    if kind == "asset":
        return str(record.code), str(record.name or ""), str(record.id)
    if kind == "exchange":
        return str(record.name), str(record.id)
    if kind == "instrument":
        return str(record.symbol), str(record.name or ""), str(record.id)
    return (
        str(record.venue_symbol or ""),
        str(record.instrument.display_symbol),
        str(record.id),
    )


def _short_id(value: Any) -> str:
    return "—" if value is None else str(value).rsplit(":", 1)[-1]


def _as_value(record: Any) -> Any:
    if is_dataclass(record):
        return {field.name: getattr(record, field.name) for field in fields(record)}
    if hasattr(record, "__dict__"):
        return vars(record)
    return record


__all__ = [
    "INSTRUMENT_TYPE_ACTIONS",
    "detail_actions",
    "detail_renderable",
    "load_instrument_markets",
    "load_records",
    "load_related",
    "record_description",
    "record_label",
    "records_renderable",
]
