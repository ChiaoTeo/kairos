"""Reference catalog actions for the Workbench product slice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from kairospy.investment.apps.reference.application import ReferenceApplication
from kairospy.system.apps.components.application.clients import ReferenceSystemClient

from ....widgets import ActionItem


INSTRUMENT_TYPE_ACTIONS = (
    ActionItem("equity", "股票", "股票和 ETF 等权益类合约", "1"),
    ActionItem("spot", "现货", "数字资产等现货交易对", "2"),
    ActionItem("perpetual", "永续合约", "没有到期日的衍生品", "3"),
    ActionItem("future", "交割合约", "具有到期日的期货合约", "4"),
    ActionItem("option", "期权", "看涨与看跌期权合约", "5"),
    ActionItem("index", "指数", "市场指数与基准", "6"),
)


def load_runtime_status(state: Any) -> dict[str, Any]:
    """Read detailed status through the Reference-owned control contract."""

    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    owner = state.owner
    client = ReferenceSystemClient(
        owner.paths.process_socket("reference"),
        database_path=owner.paths.reference_database(),
        workspace_id=str(owner.workspace_id),
    )
    return client.reference_status()


def runtime_status_renderable(value: Mapping[str, Any]) -> RenderableType:
    """Render one Reference runtime snapshot without recomputing owner health."""

    app_runtime = _mapping(value.get("app_runtime"))
    catalog = _mapping(value.get("catalog"))
    publication = _mapping(value.get("publication"))
    diagnostics = _mapping_rows(value.get("diagnostics"))
    sources = sorted(
        _mapping_rows(value.get("sources")),
        key=lambda source: (
            0 if _source_needs_attention(source) else 1,
            str(source.get("source_id") or ""),
        ),
    )

    runtime = Table.grid(padding=(0, 2))
    runtime.add_column(style="dim", no_wrap=True)
    runtime.add_column()
    runtime.add_row("整体状态", _status_text(value.get("status")))
    runtime.add_row("运行阶段", _status_text(app_runtime.get("phase")))
    runtime.add_row(
        "工作队列",
        f"active {app_runtime.get('active_work_item_count', 0)}"
        f" · queued {app_runtime.get('queued_work_item_count', 0)}",
    )
    runtime.add_row(
        "最近 Tick",
        _time_value(app_runtime.get("last_tick_finished_unix_nanos")),
    )
    runtime.add_row(
        "Tick 耗时", _duration_value(app_runtime.get("last_tick_duration_millis"))
    )
    runtime.add_row(
        "下次 Tick", _time_value(app_runtime.get("next_tick_due_unix_nanos"))
    )
    runtime_error = _mapping(app_runtime.get("last_error"))
    if runtime_error:
        runtime.add_row("最近错误", _error_text(runtime_error))

    catalog_table = Table.grid(padding=(0, 2))
    catalog_table.add_column(style="dim", no_wrap=True)
    catalog_table.add_column()
    catalog_table.add_row("Readiness", _status_text(catalog.get("readiness")))
    catalog_table.add_row(
        "Watermark",
        f"generation {catalog.get('generation', '—')}"
        f" · sequence {catalog.get('event_sequence', '—')}",
    )
    catalog_table.add_row(
        "目录规模",
        " · ".join(
            (
                f"exchange {catalog.get('exchange_count', 0)}",
                f"asset {catalog.get('asset_count', 0)}",
                f"instrument {catalog.get('instrument_count', 0)}",
                f"listing {catalog.get('listing_count', 0)}",
                f"market {catalog.get('market_count', 0)}",
                f"active {catalog.get('active_market_count', 0)}",
            )
        ),
    )
    integrity = _mapping(catalog.get("integrity"))
    catalog_table.add_row(
        "完整性",
        _integrity_summary(integrity),
    )

    source_table = Table(show_header=True, header_style="bold")
    source_table.add_column("Source")
    source_table.add_column("Provider")
    source_table.add_column("状态")
    source_table.add_column("进度")
    source_table.add_column("最近成功")
    source_table.add_column("错误")
    if sources:
        for source in sources:
            source_table.add_row(
                str(source.get("source_id") or "—"),
                str(source.get("provider_id") or "—"),
                _source_state(source),
                _progress_summary(_mapping(source.get("progress"))),
                _time_value(source.get("last_success_unix_nanos")),
                _error_text(_mapping(source.get("last_error"))),
            )
    else:
        source_table.add_row("—", "—", "无数据源", "—", "—", "—")

    publication_table = Table.grid(padding=(0, 2))
    publication_table.add_column(style="dim", no_wrap=True)
    publication_table.add_column()
    publication_table.add_row(
        "待发布", str(publication.get("pending_publication_count", 0))
    )
    publication_table.add_row(
        "积压状态",
        "degraded" if publication.get("backlog_degraded") else "正常",
    )
    publication_table.add_row(
        "最早事件", str(publication.get("oldest_pending_event_id") or "—")
    )
    publication_error = _mapping(publication.get("last_error"))
    if publication_error:
        publication_table.add_row("最近错误", _error_text(publication_error))
    if diagnostics:
        publication_table.add_row(
            "诊断",
            "\n".join(
                f"[{item.get('severity', 'unknown')}] {item.get('code', '—')}: "
                f"{item.get('message', '—')}"
                for item in diagnostics
            ),
        )
    else:
        publication_table.add_row("诊断", "无")

    return Group(
        Panel(runtime, title="Reference Runtime", border_style="cyan"),
        Panel(catalog_table, title="Catalog", border_style="cyan"),
        Panel(source_table, title=f"Sources · {len(sources)}", border_style="cyan"),
        Panel(publication_table, title="Publication", border_style="cyan"),
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


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_rows(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _status_text(value: object) -> Text:
    label = str(value or "unknown")
    normalized = label.lower()
    style = (
        "green"
        if normalized in {"ready", "healthy", "running", "active", "idle"}
        else "red"
        if normalized in {"failed", "unavailable", "not_ready"}
        else "yellow"
    )
    return Text(label, style=style)


def _time_value(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return str(value)
    try:
        unix_nanos = int(value)
    except (TypeError, ValueError):
        return str(value)
    from datetime import datetime, timezone

    rendered = datetime.fromtimestamp(unix_nanos / 1_000_000_000, tz=timezone.utc)
    return rendered.astimezone().isoformat(timespec="seconds")


def _duration_value(value: object) -> str:
    return "—" if value is None else f"{value} ms"


def _error_text(value: Mapping[str, Any]) -> str:
    if not value:
        return "—"
    code = str(value.get("code") or "error")
    message = str(value.get("message") or "—")
    retryable = " · 可重试" if value.get("retryable") else ""
    return f"{code}: {message}{retryable}"


def _source_needs_attention(source: Mapping[str, Any]) -> bool:
    phase = str(source.get("phase") or "").lower()
    return bool(
        source.get("paused")
        or source.get("stale")
        or source.get("last_error")
        or phase in {"failed", "retrying", "degraded", "unavailable"}
    )


def _source_state(source: Mapping[str, Any]) -> str:
    labels = [str(source.get("phase") or "unknown")]
    if not source.get("enabled", False):
        labels.append("disabled")
    if source.get("paused"):
        labels.append("paused")
    if source.get("stale"):
        labels.append("stale")
    failures = source.get("consecutive_failures")
    if failures:
        labels.append(f"failures={failures}")
    return " · ".join(labels)


def _progress_summary(progress: Mapping[str, Any]) -> str:
    if not progress:
        return "—"
    values = [str(progress.get("kind") or "unknown")]
    pages_done = progress.get("pages_done")
    pages_total = progress.get("pages_total")
    if pages_done is not None:
        values.append(
            f"pages {pages_done}/{pages_total}"
            if pages_total is not None
            else f"pages {pages_done}"
        )
    records_seen = progress.get("records_seen")
    records_changed = progress.get("records_changed")
    if records_seen is not None:
        values.append(f"records {records_seen}")
    if records_changed is not None:
        values.append(f"changed {records_changed}")
    return " · ".join(values)


def _integrity_summary(integrity: Mapping[str, Any]) -> str:
    if not integrity:
        return "未报告"
    issues = [
        f"{key.removesuffix('_count')}={value}"
        for key, value in integrity.items()
        if key != "degraded" and isinstance(value, int) and value > 0
    ]
    if not integrity.get("degraded") and not issues:
        return "正常"
    return "degraded" + (f" · {' · '.join(issues)}" if issues else "")


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
    "load_runtime_status",
    "record_description",
    "record_label",
    "records_renderable",
    "runtime_status_renderable",
]
