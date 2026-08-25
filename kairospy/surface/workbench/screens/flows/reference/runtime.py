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
from ...catalog import SECTION_ACTIONS
from ...session import GuidedSession
from .actions import (
    INSTRUMENT_TYPE_ACTIONS,
    detail_actions,
    detail_renderable,
    load_instrument_markets,
    load_records,
    load_related,
    records_renderable,
)
from ...navigation import (
    action_id,
    context_items,
    context_label,
    record_description,
    record_label,
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
    if kind not in {
        "assets",
        "exchanges",
        "instruments",
        "markets",
        "option-chain",
    }:
        return None
    query = " ".join(arguments).strip()
    return (_run_search(state, session, kind, query),)


def handle_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if session.context[:1] != ("reference",):
        return None
    if session.context == ("reference", "instrument-types"):
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

    if session.context == ("reference", "selected"):
        record = session.reference.selected
        kind = session.reference.kind
        if record is None or kind is None:
            session.enter("reference")
            return _choice(state, session, status="Reference 上下文已失效")
        action = action_id(detail_actions(kind), command)
        if action is None:
            return None
        if action in {"summary", "technical"}:
            body = detail_renderable(record, kind, technical=action == "technical")
            activity = _standalone_activity(
                f"{record_label(record)} · {'技术标识' if action == 'technical' else '概览'}",
                body,
            )
            return activity, *_choice(state, session, status="Reference 详情已就绪")
        operation: Callable[[], Any] = (
            (lambda: load_instrument_markets(state, record))
            if action == "markets" and kind in {"instruments", "option-chain"}
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
        session.context = ("reference", "selected")
        body = detail_renderable(record, session.reference.kind)
        return (
            _standalone_activity(f"{record_label(record)} · 概览", body),
            *_choice(state, session, status="已选择 Reference 记录"),
        )

    action = action_id(SECTION_ACTIONS["reference"], command)
    if action is None:
        return None
    if action == "instruments":
        session.reference.kind = action
        session.reference.instrument_type = None
        session.context = ("reference", "instrument-types")
        return _choice(state, session)
    session.reference.kind = action
    session.reference.instrument_type = None
    prompt = (
        "请输入标的合约 ID"
        if action == "option-chain"
        else "输入代码或名称；直接回车浏览"
    )
    session.ask(
        ActionToken(Feature.REFERENCE, "search", action),
        title=context_label(session.context, session.root_label),
        prompt=prompt,
        detail="输入 /back 或按 Esc 取消并返回当前菜单。",
    )
    return SetInteraction(session.interaction), SetStatus("等待查询条件")


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    kind = spec.route.kind
    if kind is ResultKind.REFERENCE_RECORDS:
        reference_kind = spec.route.qualifier
        assert reference_kind is not None
        records = tuple(result or ())
        if records:
            return _show_record_choices(session, records, reference_kind)
        session.enter("reference")
        interaction = ChoiceInteraction(
            title=f"{session.root_label} / 市场标的",
            summary=Text("没有找到匹配的 Reference 记录。", style="dim"),
            actions=SECTION_ACTIONS["reference"],
        )
        return SetInteraction(interaction), SetStatus("没有找到匹配结果")
    if kind is ResultKind.REFERENCE_RELATED:
        related_kind, raw_records = result
        body = records_renderable(str(related_kind), tuple(raw_records))
        return (
            _activity(spec, body),
            *_choice(state, session, status="关联记录已就绪"),
        )
    return None


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind not in _RESULT_KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
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
    instrument_type = session.reference.instrument_type
    return RunOperation(
        _spec(
            action_name=f"reference.find.{kind}",
            summary=f"查找 Reference {kind}" + (f" · {query}" if query else ""),
            route=ResultRoute(ResultKind.REFERENCE_RECORDS, kind),
            operation=lambda: load_records(
                state, kind, query, instrument_type=instrument_type
            ),
            status="正在查找 Reference 记录…",
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
            title=spec.audit_summary,
            body=body,
            copy_text=renderable_plain_text(body),
            audit_summary=spec.audit_summary,
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
    session.context = ("reference", record_kind)
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


_RESULT_KINDS = frozenset({ResultKind.REFERENCE_RECORDS, ResultKind.REFERENCE_RELATED})


__all__ = [
    "cancel_input",
    "handle_cancel",
    "handle_command",
    "handle_context",
    "handle_failure",
    "handle_input",
    "handle_success",
]
