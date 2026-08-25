"""Operations interaction flow owned by its Workbench product slice."""

from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from rich.panel import Panel
from rich.pretty import Pretty
from rich.text import Text

from ....widgets import (
    ActionToken,
    ActionItem,
    ChoiceInteraction,
    Feature,
    InputInteraction,
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
from .business import (
    BusinessPromptState,
    actions as business_actions,
    execute as execute_business,
)
from ...catalog import SECTION_ACTIONS
from ...session import GuidedSession
from .actions import (
    BUSINESS_ACTIONS,
    CONFIG_ACTIONS,
    PROFILE_ACTIONS,
    PROJECT_ACTIONS,
    SERVICE_ACTIONS,
    ProjectPromptState,
    execute_config,
    execute_operation,
    execute_project,
    execute_project_write,
    execute_service,
    list_services,
    mutate_profile,
)
from ..resources.actions import list_records as list_resource_records
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
    """Continue one typed Operations input interaction."""

    if token.feature is not Feature.OPERATIONS:
        return None
    return handle_command(state, session, token.action, (value,))


def cancel_input(session: GuidedSession, token: ActionToken) -> None:
    """Clear only the Operations prompt owned by the token."""

    if token.action.startswith("business:field:"):
        session.operations.business_prompt = None
    elif token.action.startswith("operations-project:field:"):
        session.operations.project_prompt = None
    elif token.action == "operations-profile:name":
        session.operations.profile_action = None


def handle_command(
    state: Any, session: GuidedSession, command: str, arguments: tuple[str, ...]
) -> tuple[ScreenEffect, ...] | None:
    if command == "operations-config:explain":
        name = " ".join(arguments).strip()
        return (
            _run(
                "operations.config.explain",
                f"解释配置 · {name}",
                ResultKind.OPERATIONS,
                lambda: execute_config(state, "explain", name),
            ),
        )
    if command.startswith("operations-project:field:"):
        prompt = session.operations.project_prompt
        if not isinstance(prompt, ProjectPromptState):
            session.enter("operations", "project")
            return _choice(
                state,
                session,
                Text("项目参数向导已经失效。", style="yellow"),
                "参数向导已失效",
            )
        return _accept_field(
            state,
            session,
            prompt,
            command.removeprefix("operations-project:field:"),
            " ".join(arguments),
            _advance_project,
        )
    if command == "operations-profile:name":
        action = session.operations.profile_action
        name = " ".join(arguments).strip()
        if action not in {"create", "use"} or not name:
            session.operations.profile_action = None
            session.context = ("operations", "profiles")
            return _choice(
                state,
                session,
                Text("Profile 操作或名称无效。", style="yellow"),
                "输入无效",
            )

        def operation() -> Any:
            if state.dry_run or state.no_exec:
                return {"status": "preview", "action": action, "profile": name}
            return mutate_profile(state, action, name)

        spec = _spec(
            f"operations.profile.{action}",
            f"{action} Profile {name}",
            ResultKind.OPERATIONS_PROFILE,
            operation,
        )
        return _confirm_or_run(state, session, spec)
    if command.startswith("business:field:"):
        prompt = session.operations.business_prompt
        if not isinstance(prompt, BusinessPromptState):
            session.enter("operations", "business")
            return _choice(
                state,
                session,
                Text("业务工具参数向导已经失效。", style="yellow"),
                "参数向导已失效",
            )
        return _accept_field(
            state,
            session,
            prompt,
            command.removeprefix("business:field:"),
            " ".join(arguments),
            _advance_business,
        )
    return None


def handle_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if session.context[:1] == ("operations",):
        return _operations_context(state, session, command)
    return None


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    kind = spec.route.kind
    if kind is ResultKind.OPERATIONS_SERVICES:
        records = tuple(result or ())
        session.context = ("operations", "services")
        visible = selection_records(
            records,
            label=record_label,
            description=record_description,
        )
        session.visible_records = visible
        actions = tuple(
            ActionItem(str(i), record.label, record.description, str(i))
            for i, record in enumerate(visible, 1)
        )
        interaction = ChoiceInteraction(
            title=context_label(session.context), actions=actions
        )
        session.interaction = interaction
        return SetInteraction(interaction), SetStatus(
            f"找到 {len(records)} 个结果 · 请选择"
        )
    titles = {
        ResultKind.OPERATIONS: "系统维护结果",
        ResultKind.OPERATIONS_PROJECT: "项目操作结果",
        ResultKind.OPERATIONS_PROFILE: "Profile 操作结果",
        ResultKind.BUSINESS: "业务工具结果",
    }
    title = titles.get(kind)
    if title is None:
        return None
    if kind is ResultKind.OPERATIONS_PROJECT:
        session.operations.project_prompt = None
        session.context = ("operations", "project")
    elif kind is ResultKind.OPERATIONS_PROFILE:
        session.operations.profile_action = None
        session.context = ("operations", "profiles")
    elif kind is ResultKind.BUSINESS:
        session.operations.business_prompt = None
    body = Panel(Pretty(result, expand_all=True), title=title)
    return _activity(spec, body), *_choice(state, session, status="操作已完成")


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind not in _KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
    return (
        _activity(spec, Text(error, style="red"), ActivityOutcome.FAILURE),
        *_choice(
            state,
            session,
            Text(error, style="red"),
            "操作失败 · 可重试、返回或查看帮助",
        ),
    )


def handle_cancel(
    state: Any, session: GuidedSession, spec: OperationSpec
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind not in _KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
    body = Text("操作在开始执行后被取消。", style="yellow")
    return (
        _activity(spec, body, ActivityOutcome.CANCELLED),
        *_choice(state, session, status="操作已取消 · 可继续输入"),
    )


def _operations_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    context = session.context
    if context == ("operations", "service"):
        component = session.operations.selected_service
        if component is None:
            session.enter("operations")
            return _choice(state, session)
        action = action_id(SERVICE_ACTIONS, command)
        if action is None:
            return None

        def operation() -> Any:
            if action in {"start", "stop", "restart"} and (
                state.dry_run or state.no_exec
            ):
                return {"status": "preview", "component": component, "action": action}
            return execute_service(state, component, action)

        spec = _spec(
            f"operations.service.{action}",
            f"kairos system {action} --component {component}",
            ResultKind.OPERATIONS,
            operation,
        )
        return _confirm_or_run(
            state, session, spec, dangerous=action in {"start", "stop", "restart"}
        )
    if context == ("operations", "project"):
        action = action_id(PROJECT_ACTIONS, command)
        if action is None:
            return None
        if action in {"status", "doctor"}:
            return (
                _run(
                    f"operations.project.{action}",
                    f"项目 {action}",
                    ResultKind.OPERATIONS,
                    lambda: execute_project(state, action),
                ),
            )
        prompt = ProjectPromptState(action)
        session.operations.project_prompt = prompt
        return _advance_project(state, session, prompt)
    if context == ("operations", "config"):
        action = action_id(CONFIG_ACTIONS, command)
        if action is None:
            return None
        if action == "explain":
            return _ask(
                session,
                "operations-config:explain",
                "请输入配置名称",
                "例如 launches/demo-backtest；输入 /back 取消。",
            )
        if action == "models":
            session.resources.kind = "models"
            return (
                _run(
                    "resources.models.list",
                    "查看 AI 模型连接",
                    ResultKind.RESOURCE_LIST,
                    lambda: list_resource_records(state, "models"),
                ),
            )
        if action == "profiles":
            session.context = ("operations", "profiles")
            return _choice(state, session)
        return (
            _run(
                f"operations.config.{action}",
                f"配置 {action}",
                ResultKind.OPERATIONS,
                lambda: execute_config(state, action),
            ),
        )
    if context == ("operations", "profiles"):
        action = action_id(PROFILE_ACTIONS, command)
        if action is None:
            return None
        if action == "list":
            return (
                _run(
                    "operations.profile.list",
                    "列出 Profiles",
                    ResultKind.OPERATIONS_PROFILE,
                    lambda: execute_config(state, "profiles"),
                ),
            )
        session.operations.profile_action = action
        return _ask(
            session,
            "operations-profile:name",
            "请输入 Profile 名称",
            "写入前会显示确认；输入 /back 取消。",
        )
    if context == ("operations", "business"):
        action = action_id(BUSINESS_ACTIONS, command)
        if action is None:
            return None
        session.enter("operations", "business", action)
        return _choice(state, session)
    if len(context) == 3 and context[:2] == ("operations", "business"):
        tool = context[2]
        action = action_id(business_actions(tool), command)
        if action is None:
            return None
        prompt = BusinessPromptState(tool, action)
        session.operations.business_prompt = prompt
        return _advance_business(state, session, prompt)
    if context == ("operations", "services") and session.visible_records:
        record = _record_choice(session.visible_records, command)
        if record is None:
            return None
        component = str(record.get("component") or "")
        if not component:
            return _choice(
                state,
                session,
                Text("所选服务没有组件标识。", style="yellow"),
                "服务记录无效",
            )
        session.operations.selected_service = component
        session.context = ("operations", "service")
        return _standalone_activity(
            component, Panel(Pretty(record, expand_all=True), title=component)
        ), *_choice(state, session)
    action = action_id(SECTION_ACTIONS["operations"], command)
    if action is None:
        return None
    if action == "observe":
        return (
            _run(
                "system.observe",
                "刷新系统状态",
                ResultKind.OBSERVE,
                state.refresh_snapshot,
            ),
        )
    if action in {"doctor", "workspace"}:
        return (
            _run(
                f"operations.{action}",
                f"系统维护 · {action}",
                ResultKind.OPERATIONS,
                lambda: execute_operation(state, action),
            ),
        )
    if action == "repair":

        def repair() -> Any:
            if state.dry_run or state.no_exec:
                return {"status": "preview", "action": "repair"}
            return execute_operation(state, action)

        return _confirm_or_run(
            state,
            session,
            _spec(
                "operations.repair",
                "修复 stale 运行资源",
                ResultKind.OPERATIONS,
                repair,
            ),
            dangerous=True,
        )
    if action == "services":
        return (
            _run(
                "operations.services",
                "查看系统服务",
                ResultKind.OPERATIONS_SERVICES,
                lambda: list_services(state),
            ),
        )
    if action in {"project", "config", "business"}:
        session.enter("operations", action)
        return _choice(state, session)
    return None


def _advance_project(
    state: Any, session: GuidedSession, prompt: ProjectPromptState
) -> tuple[ScreenEffect, ...]:
    next_prompt = prompt.next_prompt()
    if next_prompt:
        name, label, detail = next_prompt
        return _ask(
            session,
            f"operations-project:field:{name}",
            label,
            detail,
            Pretty(prompt.summary(), expand_all=True),
        )

    def operation() -> Any:
        return (
            {"status": "preview", **prompt.summary()}
            if state.dry_run or state.no_exec
            else execute_project_write(state, prompt)
        )

    return _confirm_or_run(
        state,
        session,
        _spec(
            f"operations.project.{prompt.action}",
            f"项目操作 {prompt.action}",
            ResultKind.OPERATIONS_PROJECT,
            operation,
        ),
        dangerous=True,
        details=Pretty(prompt.summary(), expand_all=True),
    )


def _advance_business(
    state: Any, session: GuidedSession, prompt: BusinessPromptState
) -> tuple[ScreenEffect, ...]:
    next_prompt = prompt.next_prompt()
    if next_prompt:
        name, label, detail = next_prompt
        return _ask(
            session,
            f"business:field:{name}",
            label,
            detail,
            Pretty(
                {"tool": prompt.tool, "action": prompt.action, **prompt.values},
                expand_all=True,
            ),
        )
    return (
        _run(
            f"business.{prompt.tool}.{prompt.action}",
            f"{prompt.tool} · {prompt.action}",
            ResultKind.BUSINESS,
            lambda: execute_business(state, prompt),
        ),
    )


def _accept_field(
    state: Any,
    session: GuidedSession,
    prompt: Any,
    name: str,
    raw: str,
    advance: Callable[[Any, GuidedSession, Any], tuple[ScreenEffect, ...]],
) -> tuple[ScreenEffect, ...]:
    try:
        prompt.accept(name, raw)
    except ValueError as error:
        interaction = session.interaction
        if isinstance(interaction, InputInteraction):
            interaction = InputInteraction(
                interaction.action,
                interaction.title,
                interaction.prompt,
                interaction.detail,
                interaction.value_summary,
                interaction.secret,
                str(error),
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("输入有误 · 请修正")
        return _choice(state, session, Text(str(error), style="yellow"), "输入有误")
    return advance(state, session, prompt)


def _ask(
    session: GuidedSession,
    action: str,
    prompt: str,
    detail: str,
    summary: Any | None = None,
) -> tuple[ScreenEffect, ...]:
    session.ask(
        ActionToken(Feature.OPERATIONS, action),
        title=context_label(session.context),
        prompt=prompt,
        detail=detail,
        value_summary=summary,
    )
    return SetInteraction(session.interaction), SetStatus("等待输入")


def _confirm_or_run(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
    *,
    dangerous: bool = True,
    details: Any | None = None,
) -> tuple[ScreenEffect, ...]:
    if not dangerous or state.yes or state.dry_run or state.no_exec:
        return (RunOperation(spec),)
    session.confirm(spec, display_summary=details)
    return SetInteraction(session.interaction), SetStatus("等待确认")


def _run(
    action: str, summary: str, kind: ResultKind, operation: Callable[[], Any]
) -> RunOperation:
    return RunOperation(_spec(action, summary, kind, operation))


def _spec(
    action: str, summary: str, kind: ResultKind, operation: Callable[[], Any]
) -> OperationSpec:
    return OperationSpec.create(
        action_name=action,
        audit_summary=summary,
        route=ResultRoute(kind),
        operation=operation,
        running_status="正在执行…",
    )


def _choice(
    state: Any, session: GuidedSession, summary: Any | None = None, status: str = "就绪"
) -> tuple[ScreenEffect, ...]:
    interaction = ChoiceInteraction(
        title=context_label(session.context),
        summary=summary,
        actions=context_items(session, state),
    )
    session.interaction = interaction
    return SetInteraction(interaction), SetStatus(status)


def _record_choice(records: tuple[SelectionRecord, ...], value: str) -> object | None:
    return selected_value(records, value)


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
            spec.audit_summary,
            body,
            renderable_plain_text(body),
            spec.audit_summary,
        )
    )


def _standalone_activity(title: str, body: Any) -> AppendActivity:
    return AppendActivity(
        ActivityRecord(
            f"activity-{uuid4().hex[:12]}",
            ActivityKind.QUERY,
            ActivityOutcome.SUCCESS,
            title,
            body,
            renderable_plain_text(body),
            title,
        )
    )


_KINDS = frozenset(
    {
        ResultKind.OPERATIONS_SERVICES,
        ResultKind.OPERATIONS,
        ResultKind.OPERATIONS_PROJECT,
        ResultKind.OPERATIONS_PROFILE,
        ResultKind.BUSINESS,
    }
)

__all__ = [
    "handle_cancel",
    "handle_command",
    "handle_context",
    "handle_failure",
    "handle_success",
]
