"""Reference catalog actions for the Workbench product slice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from kairospy.contracts.reference import ReferenceRuntimeStatusResponse
from kairospy.investment.apps.reference.application import ReferenceApplication
from kairospy.system.apps.components.application.clients import ReferenceSystemClient

from ....widgets import ActionItem
from ...presentation import ResultTone, conclusion, section


INSTRUMENT_TYPE_ACTIONS = (
    ActionItem("equity", "股票", "股票和 ETF 等权益类合约", "1"),
    ActionItem("spot", "现货", "数字资产等现货交易对", "2"),
    ActionItem("perpetual", "永续合约", "没有到期日的衍生品", "3"),
    ActionItem("future", "交割合约", "具有到期日的期货合约", "4"),
    ActionItem("option", "期权", "看涨与看跌期权合约", "5"),
    ActionItem("index", "指数", "市场指数与基准", "6"),
)


@dataclass(frozen=True, slots=True)
class CatalogSetupGoal:
    kind: str
    exchange_id: str | None = None
    instrument_kind: str | None = None
    underlyings: tuple[str, ...] = ()
    provider: str | None = None
    source: str | None = None

    @classmethod
    def exchange(
        cls, exchange_id: str, instrument_kind: str | None = None
    ) -> CatalogSetupGoal:
        return cls("exchange_instruments", exchange_id, instrument_kind)

    @classmethod
    def provider_product(cls, provider: str, source: str) -> CatalogSetupGoal:
        return cls("provider_product", provider=provider, source=source)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CatalogSetupGoal:
        raw_underlyings = value.get("underlyings")
        underlyings = (
            tuple(str(item) for item in raw_underlyings)
            if isinstance(raw_underlyings, (list, tuple))
            else ()
        )
        return cls(
            kind=str(value.get("kind") or "exchange_instruments"),
            exchange_id=_optional_text(value.get("exchange_id")),
            instrument_kind=_optional_text(value.get("instrument_kind")),
            underlyings=underlyings,
            provider=_binding_text(value, "provider"),
            source=_binding_text(value, "source"),
        )

    def to_request(self) -> dict[str, object]:
        if self.kind == "equity_options":
            return {"kind": self.kind, "underlyings": list(self.underlyings)}
        if self.kind == "provider_product":
            return {
                "kind": self.kind,
                "binding": {
                    "provider": self.provider or "",
                    "source": self.source or "",
                },
            }
        return {
            "kind": self.kind,
            "exchange_id": self.exchange_id or "",
            "instrument_kind": self.instrument_kind or "",
        }


@dataclass(frozen=True, slots=True)
class CatalogSourceBinding:
    provider: str
    source: str

    def to_request(self) -> dict[str, str]:
        return {"provider": self.provider, "source": self.source}


@dataclass(frozen=True, slots=True)
class CatalogSetupOption:
    binding: CatalogSourceBinding
    recommendation: str
    actual_scope: str
    requires_connection: bool
    connection_binding_present: bool
    already_configured: bool
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogPreparationProgress:
    pages_done: int | None = None
    pages_total: int | None = None
    records_seen: int | None = None
    records_changed: int | None = None
    last_success_unix_nanos: int | None = None
    retry_after_unix_nanos: int | None = None


@dataclass(frozen=True, slots=True)
class CatalogSetupPlanView:
    goal: CatalogSetupGoal
    availability: str
    activity: str
    options: tuple[CatalogSetupOption, ...]
    recommended_option: int | None
    blockers: tuple[str, ...]
    progress: CatalogPreparationProgress | None = None
    credential_binding: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CatalogSetupPlanView:
        raw_goal = value.get("goal")
        goal = (
            CatalogSetupGoal.from_mapping(raw_goal)
            if isinstance(raw_goal, Mapping)
            else CatalogSetupGoal.exchange("")
        )
        raw_options = value.get("options")
        options = (
            tuple(
                _catalog_option(item)
                for item in raw_options
                if isinstance(item, Mapping)
            )
            if isinstance(raw_options, (list, tuple))
            else ()
        )
        raw_progress = value.get("progress")
        progress = (
            _catalog_progress(raw_progress)
            if isinstance(raw_progress, Mapping)
            else None
        )
        raw_recommended = value.get("recommended_option")
        recommended = (
            raw_recommended
            if isinstance(raw_recommended, int)
            and not isinstance(raw_recommended, bool)
            else None
        )
        raw_blockers = value.get("blockers")
        blockers = (
            tuple(str(item) for item in raw_blockers)
            if isinstance(raw_blockers, (list, tuple))
            else ()
        )
        return cls(
            goal=goal,
            availability=str(value.get("availability") or "not_configured"),
            activity=str(value.get("activity") or "idle"),
            options=options,
            recommended_option=recommended,
            blockers=blockers,
            progress=progress,
            credential_binding=_optional_text(value.get("_credential_binding")),
        )

    def with_credential_binding(self, credential_binding: str) -> CatalogSetupPlanView:
        selected = self.recommended_option or 0
        options = tuple(
            replace(option, connection_binding_present=True)
            if index == selected
            else option
            for index, option in enumerate(self.options)
        )
        return replace(
            self,
            options=options,
            blockers=tuple(
                blocker
                for blocker in self.blockers
                if blocker != "missing_connection_binding"
            ),
            credential_binding=credential_binding,
        )

    def to_mapping(self) -> dict[str, object]:
        progress: dict[str, int | None] | None = None
        if self.progress is not None:
            progress = {
                "pages_done": self.progress.pages_done,
                "pages_total": self.progress.pages_total,
                "records_seen": self.progress.records_seen,
                "records_changed": self.progress.records_changed,
                "last_success_unix_nanos": self.progress.last_success_unix_nanos,
                "retry_after_unix_nanos": self.progress.retry_after_unix_nanos,
            }
        return {
            "goal": self.goal.to_request(),
            "availability": self.availability,
            "activity": self.activity,
            "recommended_option": self.recommended_option,
            "blockers": list(self.blockers),
            "options": [
                {
                    "binding": option.binding.to_request(),
                    "recommendation": option.recommendation,
                    "actual_scope": option.actual_scope,
                    "requires_connection": option.requires_connection,
                    "connection_binding_present": option.connection_binding_present,
                    "already_configured": option.already_configured,
                    "reasons": list(option.reasons),
                    "limitations": list(option.limitations),
                }
                for option in self.options
            ],
            "progress": progress,
            "_credential_binding": self.credential_binding,
        }


@dataclass(frozen=True, slots=True)
class InstrumentTradingAccess:
    markets: tuple[Any, ...]
    provider_availability: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class ReferenceSourceView:
    source_id: str
    provider_id: str
    enabled: bool
    paused: bool
    phase: str
    progress: Mapping[str, object]
    last_success_unix_nanos: int | None
    stale: bool
    consecutive_failures: int
    last_error: Mapping[str, object]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ReferenceSourceView:
        progress = value.get("progress")
        last_error = value.get("last_error")
        failures = value.get("consecutive_failures")
        last_success = value.get("last_success_unix_nanos")
        return cls(
            source_id=str(value.get("source_id") or ""),
            provider_id=str(value.get("provider_id") or ""),
            enabled=value.get("enabled") is True,
            paused=value.get("paused") is True,
            phase=str(value.get("phase") or "unknown"),
            progress=dict(progress) if isinstance(progress, Mapping) else {},
            last_success_unix_nanos=(
                last_success
                if isinstance(last_success, int) and not isinstance(last_success, bool)
                else None
            ),
            stale=value.get("stale") is True,
            consecutive_failures=(
                failures
                if isinstance(failures, int) and not isinstance(failures, bool)
                else 0
            ),
            last_error=dict(last_error) if isinstance(last_error, Mapping) else {},
        )

    @property
    def id(self) -> str:
        return self.source_id

    @property
    def label(self) -> str:
        return _source_label(self.source_id)

    @property
    def selection_description(self) -> str:
        if self.paused:
            state = "已暂停"
        else:
            state = _source_state(_source_mapping(self))
        return f"{_provider_label(self.provider_id)} · {state}"


def source_views(
    value: ReferenceRuntimeStatusResponse,
) -> tuple[ReferenceSourceView, ...]:
    payload = value.to_json_dict()
    sources = tuple(
        ReferenceSourceView.from_mapping(source)
        for source in _mapping_rows(payload.get("sources"))
    )
    return tuple(
        sorted(
            sources,
            key=lambda source: (
                not (source.stale or bool(source.last_error)),
                source.label.casefold(),
            ),
        )
    )


def source_actions(source: ReferenceSourceView | None) -> tuple[ActionItem, ...]:
    if source is None:
        return ()
    pause_action = (
        ActionItem("resume", "继续自动更新", "恢复这个目录来源的定期准备", "2")
        if source.paused
        else ActionItem("pause", "暂停自动更新", "保留当前目录，但暂不继续刷新", "2")
    )
    return (
        ActionItem("refresh", "立即更新", "现在重新读取这个来源的目录", "1"),
        pause_action,
        ActionItem("progress", "查看详细进度", "查看记录数、最近成功和失败原因", "3"),
    )


def source_detail_renderable(source: ReferenceSourceView) -> RenderableType:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    table.add_row("目录来源", _source_label(source.source_id))
    table.add_row("数据服务商", _provider_label(source.provider_id))
    table.add_row("自动更新", "已暂停" if source.paused else "已开启")
    table.add_row("当前状态", _source_state(_source_mapping(source)))
    table.add_row("准备进度", _progress_summary(source.progress))
    table.add_row("最近成功", _time_value(source.last_success_unix_nanos))
    if source.last_error:
        table.add_row("失败原因", _error_text(source.last_error))
    return Group(
        conclusion(
            "这个目录来源需要处理"
            if source.stale or source.last_error
            else "这个目录来源运行正常",
            tone=(
                ResultTone.WARNING
                if source.stale or source.last_error
                else ResultTone.SUCCESS
            ),
        ),
        section("来源状态", table),
    )


def control_catalog_source(
    state: Any, source: ReferenceSourceView, action: str
) -> Mapping[str, object]:
    if action not in {"refresh", "pause", "resume"}:
        raise ValueError("不支持的目录来源操作。")
    if state.dry_run or state.no_exec:
        return {"status": "preview", "source_id": source.source_id, "action": action}
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的项目")
    client = ReferenceSystemClient(
        state.owner.paths.process_socket("reference"),
        database_path=state.owner.paths.reference_database(),
        workspace_id=str(state.owner.workspace_id),
    )
    if action == "refresh":
        result = client.reader.refresh(source=source.source_id)
    else:
        result = client.reader.set_source_paused(
            source.source_id, paused=action == "pause"
        )
    return {
        "status": "completed",
        "source_id": source.source_id,
        "action": action,
        "result": result,
    }


def _source_mapping(source: ReferenceSourceView) -> dict[str, object]:
    return {
        "enabled": source.enabled,
        "paused": source.paused,
        "phase": source.phase,
        "stale": source.stale,
        "consecutive_failures": source.consecutive_failures,
        "last_error": source.last_error,
    }


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None and str(value) else None


def _binding_text(value: Mapping[str, object], key: str) -> str | None:
    binding = value.get("binding")
    return _optional_text(binding.get(key)) if isinstance(binding, Mapping) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _catalog_option(value: Mapping[object, object]) -> CatalogSetupOption:
    raw_binding = value.get("binding")
    binding = raw_binding if isinstance(raw_binding, Mapping) else {}
    return CatalogSetupOption(
        binding=CatalogSourceBinding(
            provider=str(binding.get("provider") or ""),
            source=str(binding.get("source") or ""),
        ),
        recommendation=str(value.get("recommendation") or "alternative"),
        actual_scope=str(value.get("actual_scope") or ""),
        requires_connection=value.get("requires_connection") is True,
        connection_binding_present=value.get("connection_binding_present") is True,
        already_configured=value.get("already_configured") is True,
        reasons=_string_tuple(value.get("reasons")),
        limitations=_string_tuple(value.get("limitations")),
    )


def _catalog_progress(value: Mapping[object, object]) -> CatalogPreparationProgress:
    return CatalogPreparationProgress(
        pages_done=_optional_int(value.get("pages_done")),
        pages_total=_optional_int(value.get("pages_total")),
        records_seen=_optional_int(value.get("records_seen")),
        records_changed=_optional_int(value.get("records_changed")),
        last_success_unix_nanos=_optional_int(value.get("last_success_unix_nanos")),
        retry_after_unix_nanos=_optional_int(value.get("retry_after_unix_nanos")),
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    return (
        tuple(str(item) for item in value) if isinstance(value, (list, tuple)) else ()
    )


def load_runtime_status(state: Any) -> ReferenceRuntimeStatusResponse:
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


def load_catalog_setup_plan(state: Any, goal: CatalogSetupGoal) -> CatalogSetupPlanView:
    """Ask Reference how one user-selected catalog can be prepared."""

    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的项目")
    owner = state.owner
    client = ReferenceSystemClient(
        owner.paths.process_socket("reference"),
        database_path=owner.paths.reference_database(),
        workspace_id=str(owner.workspace_id),
    )
    return CatalogSetupPlanView.from_mapping(
        client.plan_reference_catalog(goal.to_request())
    )


def prepare_catalog_source(
    state: Any,
    plan: CatalogSetupPlanView,
    *,
    credential_binding: str | None = None,
) -> dict[str, Any]:
    """Apply the selected Reference source and advance its first preparation tick."""

    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的项目")
    options = plan.options
    selected = plan.recommended_option or 0
    if selected >= len(options):
        raise RuntimeError("当前没有可用于准备该目录的数据来源")
    option = options[selected]
    if option.requires_connection and not credential_binding:
        raise RuntimeError("请先配置所需的数据服务账号")

    owner = state.owner
    client = ReferenceSystemClient(
        owner.paths.process_socket("reference"),
        database_path=owner.paths.reference_database(),
        workspace_id=str(owner.workspace_id),
    )
    configured = client.configure_reference_source(
        {
            "binding": option.binding.to_request(),
            "scope": {"kind": "global"},
            "desired_state": "enabled",
            "credential_binding": credential_binding,
        }
    )
    source_id = str(configured.get("source_id") or "")
    if not source_id:
        raise RuntimeError("标的目录没有返回已配置来源的标识")
    refreshed = client.reader.refresh(source=source_id)
    return {
        "source_id": source_id,
        "configured": configured,
        "refresh": refreshed,
        "plan": client.plan_reference_catalog(plan.goal.to_request()),
    }


def catalog_setup_renderable(
    value: CatalogSetupPlanView | Mapping[str, object],
) -> RenderableType:
    """Render a catalog setup plan in task language instead of runtime jargon."""

    if isinstance(value, CatalogSetupPlanView):
        value = value.to_mapping()

    availability = {
        "not_configured": "尚未准备",
        "preparing": "正在准备",
        "usable": "可以使用",
        "partially_usable": "已有部分结果可用",
        "stale": "可以使用，但需要更新",
        "unavailable": "当前不可用",
    }.get(str(value.get("availability") or ""), "状态未知")
    activity = {
        "idle": "当前没有准备任务",
        "waiting": "等待开始",
        "scanning": "正在读取来源目录",
        "promoting": "正在核对并切换新目录",
        "publishing": "正在让其他功能看到新目录",
        "retry_waiting": "失败后等待重试",
        "paused": "准备已暂停",
    }.get(str(value.get("activity") or ""), "")
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    table.add_row("当前结果", availability)
    if activity:
        table.add_row("正在进行", activity)

    options = _mapping_rows(value.get("options"))
    raw_recommended = value.get("recommended_option")
    recommended = (
        raw_recommended
        if isinstance(raw_recommended, int) and not isinstance(raw_recommended, bool)
        else 0
    )
    if options and recommended < len(options):
        option = options[recommended]
        binding = _mapping(option.get("binding"))
        provider = str(binding.get("provider") or "")
        table.add_row("建议来源", _provider_label(provider))
        scope = str(option.get("actual_scope") or "")
        table.add_row(
            "实际准备范围",
            {
                "requested_exchange": "所选交易所",
                "complete_united_states_equities": "完整美国股票目录",
                "provider_catalog": "该服务商提供的完整目录",
                "selected_underlyings": "选中的期权标的",
            }.get(scope, scope or "—"),
        )
        if "synchronizes_complete_united_states_equities" in (
            option.get("limitations") or ()
        ):
            table.add_row(
                "范围说明",
                "该来源按完整美国股票目录同步，不能只同步一个交易所。",
            )
        if "product_is_provider_specific" in (option.get("limitations") or ()):
            table.add_row(
                "目录含义",
                "这是服务商可交易目录，不代表该服务商是标的的上市交易所。",
            )
        if option.get("requires_connection") is True:
            table.add_row(
                "账号要求",
                "已配置" if option.get("connection_binding_present") else "需要先配置",
            )

    progress = _mapping(value.get("progress"))
    if progress:
        pages_done = progress.get("pages_done")
        pages_total = progress.get("pages_total")
        if pages_done is not None:
            table.add_row(
                "目录页",
                f"{pages_done} / {pages_total}"
                if pages_total is not None
                else str(pages_done),
            )
        if progress.get("records_seen") is not None:
            table.add_row("已读取", f"{progress['records_seen']} 条记录")
    return Group(table)


def _provider_label(provider: str) -> str:
    return {
        "massive": "Massive（美国证券目录）",
        "binance": "币安",
        "okx": "OKX",
        "hyperliquid": "Hyperliquid",
    }.get(provider, provider or "未知来源")


def runtime_status_renderable(value: ReferenceRuntimeStatusResponse) -> RenderableType:
    """Render one Reference runtime snapshot without recomputing owner health."""

    payload = value.to_json_dict()
    app_runtime = _mapping(payload.get("app_runtime"))
    catalog = _mapping(payload.get("catalog"))
    publication = _mapping(payload.get("publication"))
    diagnostics = _mapping_rows(payload.get("diagnostics"))
    sources = sorted(
        _mapping_rows(payload.get("sources")),
        key=lambda source: (
            0 if _source_needs_attention(source) else 1,
            str(source.get("source_id") or ""),
        ),
    )

    runtime = Table.grid(padding=(0, 2))
    runtime.add_column(style="dim", no_wrap=True)
    runtime.add_column()
    runtime.add_row("整体状态", _status_text(payload.get("status")))
    runtime.add_row("运行阶段", _status_text(app_runtime.get("phase")))
    runtime.add_row(
        "准备任务",
        f"正在执行 {app_runtime.get('active_work_item_count', 0)}"
        f" · 等待 {app_runtime.get('queued_work_item_count', 0)}",
    )
    runtime.add_row(
        "最近检查",
        _time_value(app_runtime.get("last_tick_finished_unix_nanos")),
    )
    runtime.add_row(
        "检查耗时", _duration_value(app_runtime.get("last_tick_duration_millis"))
    )
    runtime.add_row(
        "下次检查", _time_value(app_runtime.get("next_tick_due_unix_nanos"))
    )
    runtime_error = _mapping(app_runtime.get("last_error"))
    if runtime_error:
        runtime.add_row("最近错误", _error_text(runtime_error))

    catalog_table = Table.grid(padding=(0, 2))
    catalog_table.add_column(style="dim", no_wrap=True)
    catalog_table.add_column()
    catalog_table.add_row("是否可用", _status_text(catalog.get("readiness")))
    catalog_table.add_row(
        "目录规模",
        " · ".join(
            (
                f"交易所 {catalog.get('exchange_count', 0)}",
                f"资产 {catalog.get('asset_count', 0)}",
                f"交易品种 {catalog.get('instrument_count', 0)}",
                f"上市关系 {catalog.get('listing_count', 0)}",
                f"具体市场 {catalog.get('market_count', 0)}",
                f"当前有效 {catalog.get('active_market_count', 0)}",
            )
        ),
    )
    integrity = _mapping(catalog.get("integrity"))
    catalog_table.add_row(
        "完整性",
        _integrity_summary(integrity),
    )

    source_table = Table(show_header=True, header_style="bold")
    source_table.add_column("目录来源")
    source_table.add_column("数据服务商")
    source_table.add_column("状态")
    source_table.add_column("进度")
    source_table.add_column("最近成功")
    source_table.add_column("错误")
    if sources:
        for source in sources:
            source_table.add_row(
                _source_label(str(source.get("source_id") or "")),
                _provider_label(str(source.get("provider_id") or "")),
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
        "等待更新", str(publication.get("pending_publication_count", 0))
    )
    publication_table.add_row(
        "积压状态",
        "存在积压" if publication.get("backlog_degraded") else "正常",
    )
    publication_table.add_row(
        "等待最久",
        "有待处理记录" if publication.get("oldest_pending_event_id") else "无",
    )
    publication_error = _mapping(publication.get("last_error"))
    if publication_error:
        publication_table.add_row("最近错误", _error_text(publication_error))
    if diagnostics:
        publication_table.add_row(
            "诊断",
            f"发现 {len(diagnostics)} 项需要关注的问题；可到运行中心查看技术详情。",
        )
    else:
        publication_table.add_row("诊断", "无")

    ready = str(payload.get("status") or "").lower() == "ready" and not any(
        _source_needs_attention(source) for source in sources
    )
    return Group(
        conclusion(
            "标的目录可以使用" if ready else "标的目录存在需要处理的状态",
            tone=ResultTone.SUCCESS if ready else ResultTone.WARNING,
        ),
        section("准备活动", runtime),
        section("当前目录", catalog_table),
        section(f"已配置的目录来源 · {len(sources)}", source_table),
        section("让其他功能看到新目录", publication_table),
    )


def detail_actions(kind: str | None) -> tuple[ActionItem, ...]:
    if kind == "assets":
        return (
            ActionItem("summary", "概览", "查看资产名称、类型与状态", "1"),
            ActionItem("markets", "相关市场", "查找使用该资产的具体市场", "2"),
            ActionItem("technical", "技术标识", "显示完整资产标识", "3"),
        )
    if kind in {"instruments", "option-chain", "trading-access"}:
        return (
            ActionItem("summary", "概览", "查看品种类型、状态和到期信息", "1"),
            ActionItem("listings", "上市信息", "查看交易所上市记录", "2"),
            ActionItem(
                "markets",
                "在哪里可以交易",
                "区分交易所上市市场与服务商可交易渠道",
                "3",
            ),
            ActionItem("technical", "技术标识", "显示完整交易品种标识", "4"),
        )
    if kind == "exchanges":
        return (
            ActionItem("summary", "概览", "查看交易所名称与状态", "1"),
            ActionItem("related", "上市信息或市场", "查看交易所上市记录", "2"),
            ActionItem("technical", "技术标识", "显示完整交易所标识", "3"),
        )
    if kind == "markets":
        return (
            ActionItem("summary", "概览", "查看市场、交易所和计价资产", "1"),
            ActionItem("technical", "技术标识", "显示完整市场与交易品种标识", "2"),
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
    elif kind == "trading-access":
        records = application.find_instruments(
            query=query or None,
            active_only=True,
            limit=25,
        )
    elif kind == "markets":
        records = _market_search_records(application, query)
    elif kind == "option-chain":
        if not query:
            raise ValueError("请输入标的合约 ID。")
        records = application.option_chain(query, active_only=True, limit=100)
    else:
        raise RuntimeError(f"unknown reference kind: {kind}")
    return rank_records(record_kind(kind), tuple(records), query or None)[:25]


def catalog_not_initialized(error: str) -> bool:
    """Recognize a project whose local Reference catalog has not been created."""

    normalized = error.casefold()
    return "reference catalog is not initialized" in normalized or (
        "unable to open database file" in normalized
        and (
            "reference.sqlite" in normalized or "wal sidecar open failed" in normalized
        )
    )


def _market_search_records(application: Any, query: str) -> tuple[Any, ...]:
    """Resolve user text through markets, instruments, and assets."""

    markets = list(
        application.find_markets(query=query or None, active_only=True, limit=50)
    )
    if query and not markets:
        instruments = application.find_instruments(
            query=query, active_only=True, limit=25
        )
        for instrument in instruments:
            markets.extend(
                application.find_markets(
                    instrument_id=instrument.id,
                    active_only=True,
                    limit=25,
                )
            )
        assets = application.find_assets(query=query, active_only=True, limit=25)
        for asset in assets:
            markets.extend(
                application.find_markets(
                    asset_code=str(asset.code),
                    active_only=True,
                    limit=50,
                )
            )
    unique: dict[str, Any] = {}
    for market in markets:
        unique.setdefault(str(market.id), market)
    return tuple(unique.values())


def load_related(state: Any, kind: str, record: Any) -> tuple[str, tuple[Any, ...]]:
    application = _application(state)
    if kind == "assets":
        return (
            "markets",
            application.find_markets(
                asset_code=str(record.code), active_only=True, limit=10
            ),
        )
    if kind in {"instruments", "option-chain", "trading-access"}:
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
    application = _application(state)
    return (
        "instrument-access",
        (
            InstrumentTradingAccess(
                markets=application.find_markets(
                    instrument_id=record.id, active_only=True, limit=20
                ),
                provider_availability=application.find_instrument_availability(
                    instrument_ids=(record.id,), active_only=True, limit=20
                ),
            ),
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
            ("资产类型", _asset_class_label(record.asset_class)),
            ("状态", _status_label(record.status)),
        )
        technical_rows = (("资产标识", record.id),)
    elif kind == "exchanges":
        rows = (("名称", record.name), ("状态", _status_label(record.status)))
        technical_rows = (("交易所标识", record.id),)
    elif kind in {"instruments", "option-chain"}:
        rows = (
            ("代码", record.symbol),
            ("名称", record.name or "—"),
            ("品种类型", _instrument_kind_label(record.instrument_type)),
            ("状态", _status_label(record.status)),
            ("到期时间", record.expiry_unix_nanos or "—"),
            ("行权价", record.strike or "—"),
            ("期权方向", _option_right_label(record.option_right)),
        )
        technical_rows = (
            ("交易品种标识", record.id),
            ("基础品种标识", record.underlying_instrument_id or "—"),
        )
    elif kind == "markets":
        rows = (
            ("代码", record.venue_symbol or record.instrument.display_symbol),
            ("交易所", _exchange_label(record.exchange_id)),
            ("市场类型", _instrument_kind_label(record.instrument_kind)),
            ("基础资产", _short_id(record.base_asset)),
            ("计价资产", _short_id(record.quote_asset)),
            ("状态", _status_label(record.status)),
        )
        technical_rows = (
            ("市场标识", record.id),
            ("交易品种标识", record.instrument.id),
            ("上市关系标识", record.listing_id or "—"),
        )
    else:
        return Panel(Pretty(_as_value(record), expand_all=True), title="标的目录")
    for label, value in (*rows, *(technical_rows if technical else ())):
        table.add_row(label, str(value))
    return Panel(
        table,
        title=f"{record_label(record)} · {'技术标识' if technical else '概览'}",
        border_style="cyan",
    )


def records_renderable(kind: str, records: tuple[Any, ...]) -> RenderableType:
    if kind == "instrument-access" and records:
        return _instrument_access_renderable(records[0])
    titles = {
        "assets": "资产",
        "exchanges": "交易所",
        "instruments": "交易品种",
        "trading-access": "交易品种",
        "markets": "交易标的",
        "option-chain": "期权链",
        "listings": "上市信息",
    }
    if not records:
        return Panel("没有找到匹配的记录。", title=titles.get(kind, "标的目录"))
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="bold cyan")
    table.add_column("名称")
    table.add_column("说明")
    for index, record in enumerate(records[:20], 1):
        table.add_row(str(index), record_label(record), record_description(record))
    visible = min(len(records), 20)
    title = titles.get(kind, "标的目录")
    return Group(
        conclusion(f"找到 {len(records)} 条{title}记录"),
        section(title, table),
        Text(
            f"显示 {visible} 条 · 其余 {len(records) - visible} 条"
            if len(records) > visible
            else f"共 {len(records)} 条",
            style="dim",
        ),
    )


def _instrument_access_renderable(value: InstrumentTradingAccess) -> RenderableType:
    markets = Table(show_header=True, header_style="bold")
    markets.add_column("交易所")
    markets.add_column("市场代码")
    markets.add_column("状态")
    if value.markets:
        for market in value.markets:
            markets.add_row(
                _exchange_label(market.exchange_id),
                str(market.venue_symbol or market.instrument.display_symbol),
                str(market.status),
            )
    else:
        markets.add_row("—", "未找到交易所市场记录", "—")

    providers = Table(show_header=True, header_style="bold")
    providers.add_column("可用服务")
    providers.add_column("服务目录")
    providers.add_column("状态")
    if value.provider_availability:
        for availability in value.provider_availability:
            providers.add_row(
                _availability_provider_label(str(availability.source_id)),
                _source_label(str(availability.source_id)),
                _status_label(availability.instrument.status),
            )
    else:
        providers.add_row("—", "没有已同步的服务商可用性", "—")
    return Group(
        conclusion("已分别核对交易所市场与服务商渠道"),
        section("交易所上市与市场", markets),
        section("服务商可交易渠道", providers),
        Text(
            "服务商提供某只股票，不代表该服务商就是这只股票的上市交易所。",
            style="dim",
        ),
        Text(
            "服务商目录可用也不代表当前账号已有行情或下单权限；使用时仍会单独检查。",
            style="dim",
        ),
    )


def _availability_provider_label(source_id: str) -> str:
    provider = source_id.split("-", 1)[0]
    return _provider_label(provider)


def record_kind(kind: str) -> str:
    if kind == "assets":
        return "asset"
    if kind == "exchanges":
        return "exchange"
    if kind in {"instruments", "option-chain", "trading-access"}:
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
    for name in ("name", "instrument_kind", "instrument_type", "asset_class", "status"):
        value = getattr(record, name, None)
        if not value:
            continue
        if name in {"instrument_kind", "instrument_type"}:
            rendered = _instrument_kind_label(value)
        elif name == "asset_class":
            rendered = _asset_class_label(value)
        elif name == "status":
            rendered = _status_label(value)
        else:
            rendered = str(value)
        if rendered not in values:
            values.append(rendered)
    return " · ".join(values) or str(record.id)


def _application(state: Any) -> ReferenceApplication:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    database = state.owner.paths.reference_database()
    if not database.is_file():
        raise RuntimeError("reference catalog is not initialized")
    return ReferenceApplication.from_database(database)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_rows(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _status_text(value: object) -> Text:
    normalized = str(value or "unknown").lower()
    label = _status_label(value)
    style = (
        "green"
        if normalized in {"ready", "healthy", "running", "active", "trading", "idle"}
        else "red"
        if normalized
        in {"failed", "unavailable", "not_ready", "inactive", "delisted", "expired"}
        else "yellow"
    )
    return Text(label, style=style)


def _status_label(value: object) -> str:
    return {
        "ready": "可以使用",
        "healthy": "正常",
        "running": "运行中",
        "active": "当前有效",
        "trading": "可交易",
        "idle": "空闲",
        "initializing": "正在启动",
        "degraded": "需要关注",
        "failed": "失败",
        "unavailable": "不可用",
        "not_ready": "尚未就绪",
        "paused": "已暂停",
        "disabled": "已停用",
        "draft": "草稿",
        "suspended": "已暂停交易",
        "delisted": "已退市",
        "inactive": "当前无效",
        "retired": "已停用",
        "expired": "已到期",
        "unknown": "未知",
    }.get(str(value or "unknown").lower(), str(value or "未知"))


def _instrument_kind_label(value: object) -> str:
    return {
        "equity": "股票",
        "spot": "现货",
        "perpetual": "永续合约",
        "future": "交割合约",
        "option": "期权",
        "index": "指数",
        "unknown": "未知品种",
    }.get(str(value or "unknown").lower(), str(value or "未知品种"))


def _asset_class_label(value: object) -> str:
    return {
        "fiat": "法定货币",
        "crypto": "数字资产",
        "equity": "证券",
        "unknown": "未知资产",
    }.get(str(value or "unknown").lower(), str(value or "未知资产"))


def _option_right_label(value: object) -> str:
    return {
        "call": "看涨",
        "put": "看跌",
    }.get(str(value or "").lower(), "—" if not value else str(value))


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
    message = str(value.get("message") or "—")
    retryable = " · 可重试" if value.get("retryable") else ""
    return f"{message}{retryable}"


def _source_needs_attention(source: Mapping[str, Any]) -> bool:
    phase = str(source.get("phase") or "").lower()
    return bool(
        source.get("paused")
        or source.get("stale")
        or source.get("last_error")
        or phase in {"failed", "retrying", "degraded", "unavailable"}
    )


def _source_state(source: Mapping[str, Any]) -> str:
    phase = str(source.get("phase") or "unknown").lower()
    labels = [
        {
            "idle": "空闲",
            "registered": "等待开始",
            "ready": "可以使用",
            "scanning": "正在读取目录",
            "promoting": "正在核对新目录",
            "syncing": "正在准备",
            "retrying": "等待重试",
            "degraded": "更新失败，旧目录可用",
            "unavailable": "不可用",
            "paused": "已暂停",
            "disabled": "已停用",
        }.get(phase, "状态未知")
    ]
    if not source.get("enabled", False):
        labels.append("未启用")
    if source.get("paused"):
        labels.append("已暂停")
    if source.get("stale"):
        labels.append("结果可能过期")
    failures = source.get("consecutive_failures")
    if failures:
        labels.append(f"连续失败 {failures} 次")
    return " · ".join(labels)


def _progress_summary(progress: Mapping[str, Any]) -> str:
    if not progress:
        return "—"
    values = []
    pages_done = progress.get("pages_done")
    pages_total = progress.get("pages_total")
    if pages_done is not None:
        values.append(
            f"目录页 {pages_done}/{pages_total}"
            if pages_total is not None
            else f"目录页 {pages_done}"
        )
    records_seen = progress.get("records_seen")
    records_changed = progress.get("records_changed")
    if records_seen is not None:
        values.append(f"已读取 {records_seen}")
    if records_changed is not None:
        values.append(f"有变化 {records_changed}")
    return " · ".join(values) or "等待进度"


def _integrity_summary(integrity: Mapping[str, Any]) -> str:
    if not integrity:
        return "未报告"
    labels = {
        "missing_equity_market_count": "缺少股票市场",
        "legacy_exchange_market_id_count": "旧版市场标识",
        "legacy_exchange_listing_id_count": "旧版上市标识",
    }
    issues = [
        f"{labels.get(key, '异常记录')} {value}"
        for key, value in integrity.items()
        if key in labels and isinstance(value, int) and value > 0
    ]
    if not integrity.get("degraded") and not issues:
        return "正常"
    return "需要关注" + (f" · {' · '.join(issues)}" if issues else "")


def _source_label(source_id: str) -> str:
    return {
        "binance-spot": "币安现货",
        "binance-usdm-futures": "币安 U 本位合约",
        "binance-coinm-futures": "币安币本位合约",
        "binance-options": "币安期权",
        "binance-equity": "币安股票产品",
        "okx-spot": "OKX 现货",
        "okx-margin": "OKX 杠杆",
        "okx-swap": "OKX 永续合约",
        "okx-futures": "OKX 交割合约",
        "okx-options": "OKX 期权",
        "hyperliquid-spot": "Hyperliquid 现货",
        "hyperliquid-perpetual": "Hyperliquid 永续合约",
        "massive-equity": "美国股票目录",
        "massive-options": "美国股票期权目录",
    }.get(source_id, "其他目录来源")


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


def _exchange_label(value: Any) -> str:
    exchange_id = str(value or "")
    return {
        "exchange:nasdaq": "纳斯达克",
        "exchange:nyse": "纽约证券交易所",
        "exchange:amex": "美国证券交易所",
        "exchange:binance": "币安",
        "exchange:okx": "OKX",
        "exchange:hyperliquid": "Hyperliquid",
    }.get(exchange_id.lower(), _short_id(value))


def _as_value(record: Any) -> Any:
    if is_dataclass(record):
        return {field.name: getattr(record, field.name) for field in fields(record)}
    if hasattr(record, "__dict__"):
        return vars(record)
    return record


__all__ = [
    "CatalogSetupGoal",
    "CatalogSetupOption",
    "CatalogSetupPlanView",
    "INSTRUMENT_TYPE_ACTIONS",
    "catalog_not_initialized",
    "catalog_setup_renderable",
    "detail_actions",
    "detail_renderable",
    "load_instrument_markets",
    "load_catalog_setup_plan",
    "load_records",
    "load_related",
    "load_runtime_status",
    "prepare_catalog_source",
    "record_description",
    "record_label",
    "records_renderable",
    "runtime_status_renderable",
]
