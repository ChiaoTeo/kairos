"""Pure presentation helpers for the Market Workbench flow."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from ...activity import ActivityOutcome
from ...presentation import ResultTone, conclusion, count, facts, section
from ...selection import SelectionRecord, selection_records
from .actions import MarketRouteView


def observation_description(observations: Sequence[str]) -> str:
    labels = {
        "quote": "实时报价",
        "trade": "逐笔成交",
        "bar:1m": "1 分钟 K 线",
        "greeks": "期权 Greeks",
    }
    return "、".join(labels.get(value, value) for value in observations) or "行情"


def provider_description(provider: str, routes: Sequence[Mapping[str, Any]]) -> str:
    matching = tuple(
        value for value in routes if str(value.get("provider") or "") == provider
    )
    observations = tuple(
        dict.fromkeys(
            str(observation)
            for value in matching
            for observation in value.get("observation_kinds", ())
        )
    )
    content = observation_description(observations)
    return f"{content} · 可用"


def market_result(
    title: str, result: Any, *, diagnostic: bool = False
) -> RenderableType:
    labels = {
        "status": "状态",
        "state": "运行状态",
        "market_id": "Market ID",
        "instrument_id": "Instrument ID",
        "provider": "Provider",
        "data_type": "数据类型",
        "dataset_id": "Dataset ID",
        "record_count": "记录数",
        "path": "路径",
        "destination": "产物",
        "freshness": "新鲜度",
        "stale": "已过期",
        "complete": "完整",
        "reason": "原因",
        "detail": "说明",
        "error": "错误",
    }
    if isinstance(result, Mapping):
        preview = str(result.get("status") or "").lower() == "preview"
        rows = tuple(
            (label, market_result_value(result[key]))
            for key, label in labels.items()
            if key in result and result[key] is not None
        )
        return Group(
            conclusion(
                f"{title}预演完成，未执行任何修改" if preview else f"{title}已返回结果",
                tone=(
                    ResultTone.PREVIEW
                    if preview
                    else ResultTone.WARNING
                    if diagnostic and result.get("error")
                    else ResultTone.SUCCESS
                ),
            ),
            facts(rows) if rows else Text("没有更多业务字段", style="dim"),
        )
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
        table = Table("序号", "记录", show_header=True, header_style="bold")
        for index, item in enumerate(result[:20], 1):
            table.add_row(str(index), market_result_value(item))
        return Group(
            conclusion(f"{title}共 {count(len(result))} 条记录"),
            section("结果", table),
            Text(
                f"显示 {count(min(len(result), 20))} 条 · 其余 {count(max(len(result) - 20, 0))} 条",
                style="dim",
            ),
        )
    return conclusion(str(result) or f"{title}已完成")


def market_result_value(value: Any) -> str:
    if isinstance(value, Mapping):
        identity_value = (
            value.get("market_id") or value.get("dataset_id") or value.get("provider")
        )
        return str(identity_value or "结构化记录")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return f"{count(len(value))} 项"
    return str(value)


def result_outcome(result: Any) -> ActivityOutcome:
    if isinstance(result, Mapping) and result.get("status") == "preview":
        return ActivityOutcome.ATTENTION
    return ActivityOutcome.SUCCESS


def visible_records(records: tuple[Any, ...]) -> tuple[SelectionRecord, ...]:
    return selection_records(
        records,
        label=market_choice_label,
        description=market_choice_description,
    )


def market_group_summary(records: tuple[Any, ...]) -> RenderableType:
    groups: dict[str, list[str]] = {}
    for record in records:
        label = _market_group_label(record)
        exchange = _exchange_label(getattr(record, "exchange_id", None))
        exchanges = groups.setdefault(label, [])
        if exchange not in exchanges:
            exchanges.append(exchange)

    lines = Text()
    for label, exchanges in groups.items():
        market_count = sum(
            1 for record in records if _market_group_label(record) == label
        )
        if lines:
            lines.append("\n")
        lines.append(label, style="bold")
        lines.append(f" · {market_count} 个市场 · ", style="dim")
        lines.append("、".join(exchanges))
    return Group(
        Text("按交易品种归类；请选择一个具体市场。", style="dim"),
        lines,
    )


def market_choice_label(record: Any) -> str:
    return _market_group_label(record)


def market_choice_description(record: Any) -> str:
    exchange = _exchange_label(getattr(record, "exchange_id", None))
    venue_symbol = str(getattr(record, "venue_symbol", None) or "").strip()
    status = _STATUS_LABELS.get(str(getattr(record, "status", "")), "状态未知")
    values = [exchange]
    if venue_symbol:
        values.append(venue_symbol)
    values.append(status)
    return " · ".join(values)


def _market_group_label(record: Any) -> str:
    kind = str(getattr(record, "instrument_kind", "unknown"))
    kind_label = _INSTRUMENT_KIND_LABELS.get(kind, "其他品种")
    base = _asset_label(getattr(record, "base_asset", None))
    quote = _asset_label(getattr(record, "quote_asset", None))
    if base and quote:
        subject = f"{base}/{quote}"
    else:
        instrument = getattr(record, "instrument", None)
        subject = str(
            getattr(instrument, "display_symbol", None)
            or getattr(record, "venue_symbol", None)
            or "未命名品种"
        )
    return f"{subject} · {kind_label}"


def _asset_label(value: Any) -> str:
    if value is None:
        return ""
    return str(value).rsplit(":", 1)[-1]


def _exchange_label(value: Any) -> str:
    key = str(value or "").rsplit(":", 1)[-1]
    return _EXCHANGE_LABELS.get(key.lower(), key or "未知交易所")


_INSTRUMENT_KIND_LABELS = {
    "equity": "股票",
    "spot": "现货",
    "perpetual": "永续合约",
    "future": "期货",
    "option": "期权",
    "index": "指数",
}

_STATUS_LABELS = {
    "active": "当前有效",
    "trading": "正在交易",
    "inactive": "当前不可用",
    "halted": "暂停交易",
    "delisted": "已退市",
    "unknown": "状态未知",
}

_EXCHANGE_LABELS = {
    "nasdaq": "Nasdaq",
    "nyse": "NYSE",
    "amex": "AMEX",
    "binance": "Binance",
    "okx": "OKX",
    "hyperliquid": "Hyperliquid",
}


def route_views(result: Any) -> tuple[MarketRouteView, ...]:
    routes: list[MarketRouteView] = []
    for route in result or ():
        if isinstance(route, MarketRouteView):
            routes.append(route)
        elif isinstance(route, Mapping):
            routes.append(MarketRouteView.from_mapping(route))
    return tuple(routes)
