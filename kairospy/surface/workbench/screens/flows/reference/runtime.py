"""Reference interaction flow owned by its Workbench product slice."""

from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from rich.text import Text

from ....widgets import (
    ActionItem,
    ActionToken,
    ChoiceInteraction,
    Feature,
    renderable_plain_text,
)
from ...activity import ActivityKind, ActivityOutcome, ActivityRecord
from ...effects import (
    AppendActivity,
    RunOperation,
    ScreenEffect,
    SetInteraction,
    SetStatus,
)
from ...navigation.catalog import ReferenceTask, SECTION_ACTIONS
from ...session import GuidedSession
from .actions import (
    INSTRUMENT_TYPE_ACTIONS,
    ReferenceSourceView,
    catalog_not_initialized,
    control_catalog_source,
    detail_actions,
    detail_renderable,
    load_instrument_markets,
    load_records,
    load_related,
    load_runtime_status,
    records_renderable,
    record_description,
    runtime_status_renderable,
    source_actions,
    source_detail_renderable,
    source_views,
)
from ...navigation import (
    Routes,
    Section,
    action_id,
    belongs_to,
    context_items,
    context_label,
    record_label,
    route,
)
from ...operation import OperationSpec
from ...results import ResultKind, ResultRoute
from ...selection import SelectionRecord, selected_value, selection_records


def handle_input(
    state: Any, session: GuidedSession, token: ActionToken, value: str
) -> tuple[ScreenEffect, ...] | None:
    if (
        token.feature is not Feature.REFERENCE
        or token.action != "search"
        or token.field is None
    ):
        return None
    return handle_command(state, session, f"reference:{token.field}", (value,))


def cancel_input(session: GuidedSession, token: ActionToken) -> bool:
    if token.feature is not Feature.REFERENCE:
        return False
    session.reference.instrument_type = None
    return True


def handle_command(
    state: Any, session: GuidedSession, command: str, arguments: tuple[str, ...]
) -> tuple[ScreenEffect, ...] | None:
    if not command.startswith("reference:"):
        return None
    kind = command.removeprefix("reference:")
    if kind == "status":
        return (_run_status(state),)
    if kind == "sources":
        return (_run_status(state, qualifier="sources"),)
    if kind not in {
        "assets",
        "exchanges",
        "instruments",
        "markets",
        "option-chain",
        "trading-access",
    }:
        return None
    query = " ".join(arguments).strip()
    return (_run_search(state, session, kind, query),)


def handle_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if not belongs_to(session.context, Section.REFERENCE):
        return None
    if session.context == Routes.REFERENCE_INSTRUMENT_TYPES:
        instrument_type = action_id(INSTRUMENT_TYPE_ACTIONS, command)
        if instrument_type is None:
            return None
        session.reference.kind = "instruments"
        session.reference.instrument_type = instrument_type
        session.ask(
            ActionToken(Feature.REFERENCE, "search", "instruments"),
            title=context_label(session.context, session.root_label),
            prompt="输入代码或名称；直接回车浏览",
            detail="输入 /back 或按 Esc 取消并返回合约类型菜单。",
        )
        return SetInteraction(session.interaction), SetStatus("等待查询条件")

    if session.context == Routes.REFERENCE_SOURCES:
        if action_id(context_items(session, state), command) == "add":
            session.enter_context(Routes.MARKET_CATALOG_EXCHANGE)
            return _choice(state, session, status="请选择要准备的市场或交易服务")
        source = _record_choice(session.visible_records, command)
        if not isinstance(source, ReferenceSourceView):
            return None
        session.reference.selected_source = source
        session.enter_context(Routes.REFERENCE_SOURCE_SELECTED)
        return (
            _standalone_activity(
                f"{source.label} · 来源状态", source_detail_renderable(source)
            ),
            *_choice(state, session, status="已选择目录来源"),
        )

    if session.context == Routes.REFERENCE_SOURCE_SELECTED:
        source = session.reference.selected_source
        action = action_id(source_actions(source), command)
        if source is None or action is None:
            return None
        if action == "progress":
            return (
                _standalone_activity(
                    f"{source.label} · 详细进度",
                    source_detail_renderable(source),
                ),
                *_choice(state, session, status="来源进度已就绪"),
            )
        return (
            RunOperation(
                _spec(
                    action_name=f"reference.source.{action}",
                    summary=f"{source.label} · {_source_action_label(action)}",
                    route=ResultRoute(ResultKind.REFERENCE_SOURCE_CONTROL, action),
                    operation=lambda: control_catalog_source(state, source, action),
                    status="正在更新目录来源…",
                )
            ),
        )

    if session.context == Routes.REFERENCE_SELECTED:
        record = session.reference.selected
        kind = session.reference.kind
        if record is None or kind is None:
            session.restore_context(Routes.REFERENCE)
            return _choice(state, session, status="目录浏览上下文已失效")
        action = action_id(detail_actions(kind), command)
        if action is None:
            return None
        if action in {"summary", "technical"}:
            body = detail_renderable(record, kind, technical=action == "technical")
            activity = _standalone_activity(
                f"{record_label(record)} · {'技术标识' if action == 'technical' else '概览'}",
                body,
            )
            return activity, *_choice(state, session, status="目录详情已就绪")
        operation: Callable[[], Any] = (
            (lambda: load_instrument_markets(state, record))
            if action == "markets"
            and kind in {"instruments", "option-chain", "trading-access"}
            else (lambda: load_related(state, kind, record))
        )
        return (
            RunOperation(
                _spec(
                    action_name=f"reference.{kind}.{action}",
                    summary=f"{record_label(record)} · {action}",
                    route=ResultRoute(ResultKind.REFERENCE_RELATED),
                    operation=operation,
                    status="正在读取关联记录…",
                )
            ),
        )

    if len(session.context) > 1 and session.visible_records:
        record = _record_choice(session.visible_records, command)
        if record is None:
            return None
        session.reference.selected = record
        session.enter_context(Routes.REFERENCE_SELECTED)
        if session.reference.kind == "trading-access":
            return (
                RunOperation(
                    _spec(
                        action_name="reference.instrument.trading-access",
                        summary=f"查看 {record_label(record)} 在哪里可以交易",
                        route=ResultRoute(ResultKind.REFERENCE_RELATED),
                        operation=lambda: load_instrument_markets(state, record),
                        status="正在核对交易所市场与服务商渠道…",
                    )
                ),
            )
        body = detail_renderable(record, session.reference.kind)
        return (
            _standalone_activity(f"{record_label(record)} · 概览", body),
            *_choice(state, session, status="已选择目录记录"),
        )

    action = action_id(SECTION_ACTIONS[Section.REFERENCE], command)
    if action is None:
        return None
    if action == ReferenceTask.STATUS:
        return (_run_status(state),)
    if action == ReferenceTask.SOURCES:
        return (_run_status(state, qualifier="sources"),)
    if action == ReferenceTask.INSTRUMENTS:
        session.reference.kind = action
        session.reference.instrument_type = None
        session.enter_context(Routes.REFERENCE_INSTRUMENT_TYPES)
        return _choice(state, session)
    session.reference.kind = (
        "trading-access" if action == ReferenceTask.MARKETS else action
    )
    session.reference.instrument_type = None
    prompt = (
        "请输入标的合约 ID"
        if action == ReferenceTask.OPTION_CHAIN
        else "输入代码或名称；直接回车浏览"
    )
    session.ask(
        ActionToken(Feature.REFERENCE, "search", session.reference.kind),
        title=context_label(session.context, session.root_label),
        prompt=prompt,
        detail="输入 /back 或按 Esc 取消并返回当前菜单。",
    )
    return SetInteraction(session.interaction), SetStatus("等待查询条件")


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    kind = spec.route.kind
    if kind is ResultKind.REFERENCE_STATUS:
        if spec.route.qualifier == "sources":
            sources = source_views(result)
            session.reference.sources = tuple(sources)
            session.reference.selected_source = None
            session.enter_context(Routes.REFERENCE_SOURCES)
            visible = selection_records(
                sources,
                label=lambda source: source.label,
                description=lambda source: source.selection_description,
                key=lambda source: source.source_id,
            )
            session.visible_records = visible
            summary = (
                Text(f"已配置 {len(sources)} 个目录来源。请选择一个进行管理。")
                if sources
                else Text(
                    "这个项目还没有配置目录来源。可以从市场或交易服务开始添加。",
                    style="yellow",
                )
            )
            return (
                _activity(spec, runtime_status_renderable(result)),
                *_choice(state, session, summary=summary, status="目录来源已就绪"),
            )
        body = runtime_status_renderable(result)
        return (
            _activity(spec, body),
            *_choice(state, session, status="标的目录准备状态已就绪"),
        )
    if kind is ResultKind.REFERENCE_RECORDS:
        reference_kind = spec.route.qualifier
        assert reference_kind is not None
        records = tuple(result or ())
        if records:
            return _show_record_choices(session, records, reference_kind)
        if reference_kind == "trading-access":
            session.market.query = session.reference.query
            session.enter_context(Routes.MARKET_MISSING)
            message = Text(
                "当前目录里还没有这个交易品种。"
                "你可以选择交易所或交易服务，Kairos 会推荐合适的目录来源。",
                style="yellow",
            )
            interaction = ChoiceInteraction(
                title=context_label(session.context, session.root_label),
                summary=message,
                actions=context_items(session, state),
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("尚未找到这个交易品种")
        session.restore_context(Routes.REFERENCE)
        interaction = ChoiceInteraction(
            title=f"{session.root_label} / 市场标的",
            summary=Text("没有找到匹配的目录记录。", style="dim"),
            actions=SECTION_ACTIONS[Section.REFERENCE],
        )
        return SetInteraction(interaction), SetStatus("没有找到匹配结果")
    if kind is ResultKind.REFERENCE_RELATED:
        related_kind, raw_records = result
        body = records_renderable(str(related_kind), tuple(raw_records))
        return (
            _activity(spec, body),
            *_choice(state, session, status="关联记录已就绪"),
        )
    if kind is ResultKind.REFERENCE_SOURCE_CONTROL:
        action = spec.route.qualifier or "更新"
        action_label = {
            "refresh": "立即更新",
            "pause": "暂停自动更新",
            "resume": "继续自动更新",
        }.get(action, action)
        body = Text(f"{action_label}请求已提交。", style="green")
        return _activity(spec, body), _run_status(state, qualifier="sources")
    return None


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind not in _RESULT_KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
    if spec.route.kind is ResultKind.REFERENCE_RECORDS and catalog_not_initialized(
        error
    ):
        session.market.query = session.reference.query
        session.enter_context(Routes.MARKET_MISSING)
        message = Text(
            "这个项目还没有可查询的标的目录。"
            "你可以选择交易所和品种，Kairos 会推荐合适的数据服务并带你完成准备。",
            style="yellow",
        )
        interaction = ChoiceInteraction(
            title=context_label(session.context, session.root_label),
            summary=message,
            actions=context_items(session, state),
        )
        session.interaction = interaction
        return (
            _activity(spec, message, ActivityOutcome.FAILURE),
            SetInteraction(interaction),
            SetStatus("标的目录尚未准备"),
        )
    message = Text(error, style="red")
    return (
        _activity(spec, message, ActivityOutcome.FAILURE),
        *_choice(
            state,
            session,
            summary=message,
            status="操作失败 · 可重试、返回或查看帮助",
        ),
    )


def handle_cancel(
    state: Any, session: GuidedSession, spec: OperationSpec
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind not in _RESULT_KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
    body = Text("操作在开始执行后被取消。", style="yellow")
    return (
        _activity(spec, body, ActivityOutcome.CANCELLED),
        *_choice(state, session, status="操作已取消 · 可继续输入"),
    )


def _run_search(
    state: Any, session: GuidedSession, kind: str, query: str
) -> RunOperation:
    session.reference.query = query or None
    instrument_type = session.reference.instrument_type
    return RunOperation(
        _spec(
            action_name=f"reference.find.{kind}",
            summary="查找标的目录" + (f" · {query}" if query else ""),
            route=ResultRoute(ResultKind.REFERENCE_RECORDS, kind),
            operation=lambda: load_records(
                state, kind, query, instrument_type=instrument_type
            ),
            status="正在查找标的目录…",
        )
    )


def _run_status(state: Any, *, qualifier: str | None = None) -> RunOperation:
    return RunOperation(
        _spec(
            action_name="reference.status",
            summary="查看标的目录准备状态",
            route=ResultRoute(ResultKind.REFERENCE_STATUS, qualifier),
            operation=lambda: load_runtime_status(state),
            status="正在读取标的目录准备状态…",
        )
    )


def _choice(
    state: Any,
    session: GuidedSession,
    *,
    summary: Any | None = None,
    status: str = "就绪",
) -> tuple[ScreenEffect, ...]:
    interaction = ChoiceInteraction(
        title=context_label(session.context, session.root_label),
        summary=summary,
        actions=context_items(session, state),
    )
    session.interaction = interaction
    return SetInteraction(interaction), SetStatus(status)


def _record_choice(records: tuple[SelectionRecord, ...], value: str) -> object | None:
    return selected_value(records, value)


def _source_action_label(action: str) -> str:
    return {
        "refresh": "立即更新",
        "pause": "暂停自动更新",
        "resume": "继续自动更新",
    }.get(action, "更新目录来源")


def _spec(
    *,
    action_name: str,
    summary: str,
    route: ResultRoute,
    operation: Callable[[], Any],
    status: str,
) -> OperationSpec:
    return OperationSpec.create(
        action_name=action_name,
        audit_summary=summary,
        route=route,
        operation=operation,
        running_status=status,
    )


def _activity(
    spec: OperationSpec,
    body: Any,
    outcome: ActivityOutcome = ActivityOutcome.SUCCESS,
) -> AppendActivity:
    return AppendActivity(
        ActivityRecord(
            activity_id=spec.operation_id,
            kind=ActivityKind.QUERY,
            outcome=outcome,
            title=spec.display_title,
            body=body,
            copy_text=renderable_plain_text(body),
            audit_summary=spec.audit_summary,
            scope_label=spec.scope_label,
        )
    )


def _standalone_activity(title: str, body: Any) -> AppendActivity:
    return AppendActivity(
        ActivityRecord(
            activity_id=f"activity-{uuid4().hex[:12]}",
            kind=ActivityKind.QUERY,
            outcome=ActivityOutcome.SUCCESS,
            title=title,
            body=body,
            copy_text=renderable_plain_text(body),
            audit_summary=title,
        )
    )


def _show_record_choices(
    session: GuidedSession, records: tuple[Any, ...], record_kind: str
) -> tuple[ScreenEffect, ...]:
    session.enter_context(route(Section.REFERENCE, record_kind))
    visible = selection_records(
        records,
        label=record_label,
        description=record_description,
    )
    session.visible_records = visible
    actions = tuple(
        ActionItem(
            str(index),
            record.label,
            record.description,
            str(index),
        )
        for index, record in enumerate(visible, 1)
    )
    interaction = ChoiceInteraction(
        title=f"{session.root_label} / 市场标的", actions=actions
    )
    return (
        SetInteraction(interaction),
        SetStatus(f"找到 {len(records)} 个结果 · 请选择"),
    )


_RESULT_KINDS = frozenset(
    {
        ResultKind.REFERENCE_RECORDS,
        ResultKind.REFERENCE_RELATED,
        ResultKind.REFERENCE_STATUS,
        ResultKind.REFERENCE_SOURCE_CONTROL,
    }
)


__all__ = [
    "cancel_input",
    "handle_cancel",
    "handle_command",
    "handle_context",
    "handle_failure",
    "handle_input",
    "handle_success",
]
