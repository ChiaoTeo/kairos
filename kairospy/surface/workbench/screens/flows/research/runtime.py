"""Research interaction flow owned by its Workbench product slice."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Callable

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from ....widgets import ActionToken, ChoiceInteraction, Feature, renderable_plain_text
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
    DATA_ACTIONS,
    RESEARCH_ACTIONS,
    execute as execute_research,
    preview as preview_research,
)
from ...navigation import action_id, context_items, context_label
from ...operation import OperationSpec
from ...results import ResultKind, ResultRoute
from ...presentation import ResultTone, conclusion, count, facts, section


def handle_input(
    state: Any, session: GuidedSession, token: ActionToken, value: str
) -> tuple[ScreenEffect, ...] | None:
    if token.feature is not Feature.RESEARCH:
        return None
    return handle_command(state, session, token.action, (value,))


def cancel_input(session: GuidedSession, token: ActionToken) -> bool:
    if token.feature is not Feature.RESEARCH:
        return False
    session.research.reset()
    return True


def handle_command(
    state: Any, session: GuidedSession, command: str, arguments: tuple[str, ...]
) -> tuple[ScreenEffect, ...] | None:
    if not command.startswith("research:"):
        return None
    return _handle_command(
        state, session, command.removeprefix("research:"), " ".join(arguments)
    )


def handle_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if session.context[:1] != ("research",):
        return None
    if len(session.context) == 1:
        action = action_id(SECTION_ACTIONS["research"], command)
        if action is None:
            return None
        session.enter("research", action)
        return _choice(state, session)
    items = DATA_ACTIONS if session.context[1] == "data" else RESEARCH_ACTIONS
    action = action_id(items, command)
    if action is None:
        return None
    if action in {"datasets", "sets"}:
        return (_research_run(state, action, None, None),)
    prompts = {
        "inspect": "请输入 Dataset ID",
        "plan-data": "请输入 requirements.json 路径",
        "execute-data": "请输入 requirements.json 路径",
        "execution": "请输入 plan hash",
        "set": "请输入 Dataset Set alias",
        "data-gate": "请输入 composition hash",
        "lock-plan": "请输入 research-plan.json 路径",
        "show-plan": "请输入 plan hash",
        "publish-gate": "请输入 research-plan.json 路径",
        "show-gate": "请输入 plan hash",
    }
    return _ask(session, f"research:{action}", prompts[action], "输入 /back 取消。")


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind is not ResultKind.RESEARCH:
        return None
    body = _research_result(spec.action_name, result)
    outcome = (
        ActivityOutcome.ATTENTION
        if isinstance(result, Mapping) and result.get("status") == "preview"
        else ActivityOutcome.SUCCESS
    )
    return _activity(spec, body, outcome), *_choice(state, session, status="操作已完成")


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind is not ResultKind.RESEARCH:
        return None
    session.clear_result_flow(spec.route.kind)
    message = Text(error, style="red")
    return (
        _activity(spec, message, ActivityOutcome.FAILURE),
        *_choice(
            state,
            session,
            message,
            "操作失败 · 可重试、返回或查看帮助",
        ),
    )


def handle_cancel(
    state: Any, session: GuidedSession, spec: OperationSpec
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind is not ResultKind.RESEARCH:
        return None
    session.clear_result_flow(spec.route.kind)
    body = Text("操作在开始执行后被取消。", style="yellow")
    return (
        _activity(spec, body, ActivityOutcome.CANCELLED),
        *_choice(state, session, status="操作已取消 · 可继续输入"),
    )


def _handle_command(
    state: Any, session: GuidedSession, action: str, value: str
) -> tuple[ScreenEffect, ...]:
    if action in {"execute-data", "publish-gate"}:
        session.research.action = action
        session.research.primary = value
        suffix = (
            "execute-data-hash" if action == "execute-data" else "publish-gate-evidence"
        )
        prompt = (
            "请输入已审阅的 plan hash；直接回车可跳过"
            if action == "execute-data"
            else "请输入 research-evidence.json 路径"
        )
        return _ask(session, f"research:{suffix}", prompt, "输入 /back 取消。")
    if action == "execute-data-hash":
        return _research_confirm(
            state, session, "execute-data", session.research.primary, value or None
        )
    if action == "publish-gate-evidence":
        return _research_confirm(
            state, session, "publish-gate", session.research.primary, value
        )
    if action == "lock-plan":
        return _research_confirm(state, session, action, value, None)
    return (_research_run(state, action, value, None),)


def _research_confirm(
    state: Any,
    session: GuidedSession,
    action: str,
    value: str | None,
    extra: str | None,
) -> tuple[ScreenEffect, ...]:
    session.research.reset()
    spec = _spec(
        f"research.{action}",
        f"kairos research {action} {value or ''}".strip(),
        lambda: (
            preview_research(action, value, extra)
            if state.dry_run or state.no_exec
            else execute_research(state, action, value, extra)
        ),
    )
    return _confirm_or_run(state, session, spec)


def _research_run(
    state: Any, action: str, value: str | None, extra: str | None
) -> RunOperation:
    return RunOperation(
        _spec(
            f"research.{action}",
            f"数据研究 · {action}",
            lambda: execute_research(state, action, value, extra),
        )
    )


def _ask(
    session: GuidedSession, action: str, prompt: str, detail: str
) -> tuple[ScreenEffect, ...]:
    session.ask(
        ActionToken(Feature.RESEARCH, action),
        title=context_label(session.context, session.root_label),
        prompt=prompt,
        detail=detail,
    )
    return SetInteraction(session.interaction), SetStatus("等待输入")


def _confirm_or_run(
    state: Any, session: GuidedSession, spec: OperationSpec
) -> tuple[ScreenEffect, ...]:
    if state.yes or state.dry_run or state.no_exec:
        return (RunOperation(spec),)
    session.confirm(spec)
    return SetInteraction(session.interaction), SetStatus("等待确认")


def _spec(action: str, summary: str, operation: Callable[[], Any]) -> OperationSpec:
    return OperationSpec.create(
        action_name=action,
        audit_summary=summary,
        route=ResultRoute(ResultKind.RESEARCH),
        operation=operation,
        running_status="正在执行…",
    )


def _choice(
    state: Any,
    session: GuidedSession,
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


def _research_result(action_name: str, result: Any) -> RenderableType:
    action = action_name.rsplit(".", 1)[-1]
    labels = {
        "status": "状态",
        "action": "操作",
        "value": "输入",
        "dataset_id": "Dataset ID",
        "dataset_set": "Dataset Set",
        "plan_hash": "Plan hash",
        "composition_hash": "Composition hash",
        "requirements_hash": "Requirements hash",
        "evidence_path": "证据",
        "destination": "产物",
        "record_count": "记录数",
        "eligible": "门禁通过",
        "reason": "原因",
        "detail": "说明",
    }
    if isinstance(result, Mapping):
        preview = str(result.get("status") or "").lower() == "preview"
        rows = tuple(
            (label, _research_value(result[key]))
            for key, label in labels.items()
            if key in result and result[key] is not None
        )
        return Group(
            conclusion(
                f"数据研究 {action} 预演完成，未执行任何修改"
                if preview
                else f"数据研究 {action} 已返回结果",
                tone=ResultTone.PREVIEW if preview else ResultTone.SUCCESS,
            ),
            facts(rows) if rows else Text("没有更多业务字段", style="dim"),
        )
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
        table = Table("序号", "记录", show_header=True, header_style="bold")
        for index, item in enumerate(result[:20], 1):
            table.add_row(str(index), _research_value(item))
        return Group(
            conclusion(f"数据研究 {action} 返回 {count(len(result))} 条记录"),
            section("结果", table),
            Text(
                f"显示 {count(min(len(result), 20))} 条 · 其余 {count(max(len(result) - 20, 0))} 条",
                style="dim",
            ),
        )
    return conclusion(str(result) or f"数据研究 {action} 已完成")


def _research_value(value: Any) -> str:
    if isinstance(value, Mapping):
        identity_value = (
            value.get("dataset_id")
            or value.get("plan_hash")
            or value.get("composition_hash")
        )
        return str(identity_value or "结构化记录")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return f"{count(len(value))} 项"
    return str(value)


def _activity(
    spec: OperationSpec,
    body: Any,
    outcome: ActivityOutcome = ActivityOutcome.SUCCESS,
) -> AppendActivity:
    return AppendActivity(
        ActivityRecord(
            spec.operation_id,
            ActivityKind.OPERATION,
            outcome,
            spec.display_title,
            body,
            renderable_plain_text(body),
            spec.audit_summary,
            scope_label=spec.scope_label,
        )
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
