"""Operations interaction flow owned by its Workbench product slice."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Callable
from uuid import uuid4

from rich.panel import Panel
from rich.pretty import Pretty
from rich.text import Text
from kairospy.system.apps.components.application import ComponentProcessApplication

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
    RefreshOperationsLogs,
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
    ProjectPromptState,
    project_actions,
    execute_config,
    execute_operation,
    execute_project,
    execute_project_write,
    execute_service,
    list_services,
    mutate_profile,
)
from .views import (
    LOG_FOLLOW_ACTIONS,
    SUPPORT_ACTIONS,
    SupportStatusView,
    ServiceStatusView,
    diagnostics_renderable,
    service_actions,
    service_display_name,
    service_status_line,
    service_status_view,
    service_summary,
    operations_overview,
    operations_group_records,
    operations_records,
    support_summary,
    support_diagnostics,
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


def cancel_input(session: GuidedSession, token: ActionToken) -> bool:
    """Clear only the Operations prompt owned by the token."""

    if token.feature is not Feature.OPERATIONS:
        return False
    if token.action.startswith("business:field:"):
        session.operations.business_prompt = None
    elif token.action.startswith("operations-project:field:"):
        session.operations.project_prompt = None
    elif token.action == "operations-profile:name":
        session.operations.profile_action = None
    return True


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
            session.enter("project")
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
    if session.context == ("project",) or session.context[:1] == ("operations",):
        return _operations_context(state, session, command)
    return None


def enter_overview(state: Any, session: GuidedSession) -> tuple[ScreenEffect, ...]:
    """Open the Operations Center by reading its current runtime inventory."""

    session.enter("operations", "overview")
    return (
        _run(
            "operations.overview",
            "查看运行中心",
            ResultKind.OPERATIONS_OVERVIEW,
            state.refresh_snapshot,
        ),
    )


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    kind = spec.route.kind
    if kind is ResultKind.OPERATIONS_OVERVIEW:
        if result is None:
            session.context = ("operations", "overview")
            session.visible_records = ()
            return _choice(
                state,
                session,
                Text(
                    state.load_error or "当前项目的运行状态不可用",
                    style="yellow",
                ),
                "运行结构读取失败 · 可刷新或返回",
            )
        inventory = operations_records(result)
        records = operations_group_records(inventory)
        session.context = ("operations", "overview")
        session.visible_records = records
        session.operations.inventory_records = inventory
        session.operations.group_records = records
        interaction = ChoiceInteraction(
            title=context_label(session.context),
            summary=operations_overview(result),
            actions=tuple(
                ActionItem(str(index), record.label, record.description, str(index))
                for index, record in enumerate(records, 1)
            ),
        )
        session.interaction = interaction
        return SetInteraction(interaction), SetStatus(
            f"已读取 {len(records)} 个运行对象 · 请选择"
        )
    if kind is ResultKind.OPERATIONS_SERVICES:
        records = tuple(service_status_view(record) for record in (result or ()))
        session.context = ("operations", "services")
        visible = selection_records(
            records,
            label=lambda record: record.display_name,
            description=lambda record: f"{record.state_label} · {record.summary}",
        )
        session.visible_records = visible
        session.operations.service_records = visible
        actions = tuple(
            ActionItem(str(i), record.label, record.description, str(i))
            for i, record in enumerate(visible, 1)
        )
        interaction = ChoiceInteraction(
            title=context_label(session.context),
            actions=actions,
        )
        session.interaction = interaction
        return SetInteraction(interaction), SetStatus(
            f"找到 {len(records)} 个结果 · 请选择"
        )
    titles = {
        ResultKind.OPERATIONS: "服务操作结果",
        ResultKind.OPERATIONS_PROJECT: "项目操作结果",
        ResultKind.OPERATIONS_PROFILE: "Profile 操作结果",
        ResultKind.BUSINESS: "业务工具结果",
    }
    title = titles.get(kind)
    if title is None:
        return None
    if kind is ResultKind.OPERATIONS_PROJECT:
        session.home()
        if spec.action_name.endswith(".scaffold") or state.owner is None:
            session.enter("project")
    elif kind is ResultKind.OPERATIONS_PROFILE:
        session.operations.profile_action = None
        session.context = ("operations", "profiles")
    elif kind is ResultKind.BUSINESS:
        session.operations.business_prompt = None
    if kind is ResultKind.OPERATIONS and spec.route.qualifier == "service-status":
        view = service_status_view(result)
        session.operations.selected_service_status = view
        body = service_summary(view)
    elif (
        kind is ResultKind.OPERATIONS and spec.route.qualifier == "service-diagnostics"
    ):
        current = session.operations.selected_service_status
        raw = dict(current.raw) if current is not None else {}
        if isinstance(result, Mapping):
            raw.update(result)
        raw.setdefault("component", session.operations.selected_service or "unknown")
        body = diagnostics_renderable(service_status_view(raw))
    elif kind is ResultKind.OPERATIONS and spec.route.qualifier == "service-logs":
        lines = result.get("lines", ()) if isinstance(result, Mapping) else ()
        body = Text("\n".join(str(line) for line in lines) or "当前没有日志。")
    elif kind is ResultKind.OPERATIONS and spec.route.qualifier == "support-logs":
        lines = result.get("lines", ()) if isinstance(result, Mapping) else ()
        body = Text("\n".join(str(line) for line in lines) or "当前没有日志。")
    elif (
        kind is ResultKind.OPERATIONS and spec.route.qualifier == "support-diagnostics"
    ):
        view = session.operations.selected_support_status
        body = (
            support_diagnostics(view)
            if view is not None
            else Text("支撑进程上下文已经失效。", style="yellow")
        )
    else:
        body = Panel(Pretty(result, expand_all=True), title=title)
    return _activity(spec, body), *_choice(state, session, status="操作已完成")


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind not in _KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
    if spec.route.qualifier in {
        "service-status",
        "service-diagnostics",
        "service-logs",
        "support-logs",
        "support-diagnostics",
    }:
        current = session.operations.selected_service_status
        suggestion = (
            current.recommendation
            if current is not None
            else "查看最近日志或技术诊断后重试。"
        )
        body = Text()
        body.append(f"原因：{error}", style="red")
        body.append(f"\n\n建议：{suggestion}", style="yellow")
        return (
            _activity(spec, body, ActivityOutcome.FAILURE),
            *_choice(
                state,
                session,
                body,
                "操作失败 · 请选择恢复动作",
            ),
        )
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
    if context[:2] == ("operations", "support"):
        action = action_id(SUPPORT_ACTIONS, command)
        if action == "refresh":
            return enter_overview(state, session)
        if action is None:
            return None
        name = session.operations.selected_support
        if name is None:
            return enter_overview(state, session)
        if action == "logs":
            return (
                _run(
                    "operations.support.logs",
                    f"查看 {name} 日志",
                    ResultKind.OPERATIONS,
                    lambda: ComponentProcessApplication(state.owner).log_snapshot(name),
                    qualifier="support-logs",
                ),
            )
        return (
            _run(
                "operations.support.diagnostics",
                f"查看 {name} 技术证据",
                ResultKind.OPERATIONS,
                lambda: session.operations.selected_support_status,
                qualifier="support-diagnostics",
            ),
        )
    if context == ("operations", "overview") and session.visible_records:
        target = _record_choice(session.visible_records, command)
        if target is None:
            if command in {"refresh", "r"}:
                return enter_overview(state, session)
            return None
        if not isinstance(target, Mapping):
            return None
        kind = target.get("kind")
        if kind == "group":
            name = str(target.get("name") or "")
            records = target.get("records")
            if name not in {"services", "instances", "supports"} or not isinstance(
                records, tuple
            ):
                return None
            session.context = ("operations", name)
            session.visible_records = records
            interaction = ChoiceInteraction(
                title=context_label(session.context),
                summary=(
                    Text("当前没有运行对象", style="dim") if not records else None
                ),
                actions=tuple(
                    ActionItem(str(i), record.label, record.description, str(i))
                    for i, record in enumerate(records, 1)
                ),
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("就绪")
        return None
    if (
        context
        in {
            ("operations", "services"),
            ("operations", "instances"),
            ("operations", "supports"),
        }
        and session.visible_records
    ):
        target = _record_choice(session.visible_records, command)
        if target is None:
            return None
        if not isinstance(target, Mapping):
            return None
        kind = target.get("kind")
        if kind == "service":
            view = target.get("value")
            if not isinstance(view, ServiceStatusView):
                return None
            session.operations.selected_service = view.component
            session.operations.selected_service_status = view
            session.visible_records = ()
            session.context = ("operations", "service", view.component)
            return _choice(state, session, status=service_status_line(view))
        if kind == "run-instance" and isinstance(target.get("value"), Mapping):
            from ..launch.runtime import enter_selected_instance

            return enter_selected_instance(state, session, target["value"])
        if kind == "support":
            name = str(target.get("name") or "support")
            value = target.get("value")
            if not isinstance(value, SupportStatusView):
                return None
            session.visible_records = ()
            session.operations.selected_support = name
            session.operations.selected_support_status = value
            session.context = ("operations", "support", name)
            return _choice(
                state,
                session,
                support_summary(value),
            )
        return None
    if context[:2] == ("operations", "service-logs"):
        component = session.operations.selected_service
        buffer = session.operations.live_buffer
        if component is None or buffer is None:
            session.context = ("operations", "services")
            return _choice(state, session, status="日志上下文已经失效")
        action = action_id(LOG_FOLLOW_ACTIONS, command)
        if action is None:
            return None
        if action == "pause":
            if buffer.following:
                buffer.pause()
                status = "实时日志已暂停"
            else:
                buffer.resume()
                status = "实时日志继续跟随"
            return RefreshOperationsLogs(False), *log_control_effects(session, status)
        if action == "clear":
            buffer.clear_visible()
            return RefreshOperationsLogs(False, True), *log_control_effects(
                session, "当前日志窗口已清空"
            )
        return RefreshOperationsLogs(True), *log_control_effects(
            session, "正在刷新实时日志"
        )
    if context[:2] == ("operations", "service"):
        component = session.operations.selected_service
        if component is None:
            session.enter("operations")
            return _choice(state, session)
        action = action_id(
            service_actions(session.operations.selected_service_status), command
        )
        if action is None:
            return None
        display_name = service_display_name(component)

        if action == "follow":
            session.operations.start_logs(component, started_at=time.monotonic())
            session.context = ("operations", "service-logs", component)
            return RefreshOperationsLogs(True, True), *log_control_effects(
                session, "实时日志 · 后台刷新中"
            )

        def operation() -> Any:
            if action in {"start", "stop", "restart", "repair", "repair-start"} and (
                state.dry_run or state.no_exec
            ):
                return {"status": "preview", "component": component, "action": action}
            return execute_service(state, component, action)

        spec = _spec(
            f"operations.service.{action}",
            f"kairos system {action} --component {component}",
            ResultKind.OPERATIONS,
            operation,
            qualifier=(
                "service-diagnostics"
                if action == "diagnostics"
                else "service-logs"
                if action == "logs"
                else "service-status"
            ),
            running_status={
                "start": f"正在启动{display_name} · 创建进程并等待控制端点就绪…",
                "stop": f"正在停止{display_name} · 请求安全停止并释放运行资源…",
                "restart": f"正在重启{display_name} · 停止旧进程后等待新进程就绪…",
                "repair": f"正在清理{display_name}的失效运行资源…",
                "repair-start": f"正在清理并启动{display_name}…",
            }.get(action, "正在读取服务信息…"),
        )
        return _confirm_or_run(
            state,
            session,
            spec,
            dangerous=action in {"start", "stop", "restart", "repair", "repair-start"},
        )
    if context in {("project",), ("operations", "project")}:
        action = action_id(
            project_actions(has_project=state.owner is not None), command
        )
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
                    "查看模型连接",
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
        view = service_status_view(record)
        component = view.component
        if not component:
            return _choice(
                state,
                session,
                Text("所选服务没有组件标识。", style="yellow"),
                "服务记录无效",
            )
        session.operations.selected_service = component
        session.operations.selected_service_status = view
        session.visible_records = ()
        session.context = ("operations", "service", component)
        return _choice(state, session, status=service_status_line(view))
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
    if action == "doctor":
        return (
            _run(
                f"operations.{action}",
                f"运行中心 · {action}",
                ResultKind.OPERATIONS,
                lambda: execute_operation(state, action),
            ),
        )
    if action == "services":
        return (
            _run(
                "operations.services",
                "查看后台服务",
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
    action: str,
    summary: str,
    kind: ResultKind,
    operation: Callable[[], Any],
    *,
    qualifier: str | None = None,
) -> RunOperation:
    return RunOperation(_spec(action, summary, kind, operation, qualifier=qualifier))


def _spec(
    action: str,
    summary: str,
    kind: ResultKind,
    operation: Callable[[], Any],
    *,
    qualifier: str | None = None,
    running_status: str = "正在执行…",
) -> OperationSpec:
    return OperationSpec.create(
        action_name=action,
        audit_summary=summary,
        route=ResultRoute(kind, qualifier),
        operation=operation,
        running_status=running_status,
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


def service_log_operation(
    state: Any, session: GuidedSession
) -> Callable[[], Any] | None:
    """Return a finite tail read for the selected service log stream."""

    component = session.operations.selected_service
    if component is None or session.context[:2] != ("operations", "service-logs"):
        return None
    return lambda: execute_service(state, component, "log-tail")


def log_control_effects(
    session: GuidedSession, status: str
) -> tuple[ScreenEffect, ...]:
    component = session.operations.selected_service or "服务"
    buffer = session.operations.live_buffer
    if buffer is None:
        detail = Text("等待首次日志刷新…", style="dim")
        refreshing = False
    else:
        detail = Text(
            f"可见 {len(buffer.lines)} 行 · 未读 {buffer.unseen_lines}"
            f" · 已丢弃 {buffer.dropped_lines}\n"
            f"完整日志 {buffer.full_log_path or '尚未生成'}",
            style="dim",
        )
        refreshing = buffer.following
    session.control(
        f"{service_display_name(component)}实时日志",
        detail,
        LOG_FOLLOW_ACTIONS,
        refreshing=refreshing,
    )
    return SetInteraction(session.interaction), SetStatus(status)


def finish_log_follow(session: GuidedSession) -> AppendActivity | None:
    """Close transient log state and retain one compact terminal summary."""

    component = session.operations.selected_service
    buffer = session.operations.live_buffer
    started_at = session.operations.log_started_at
    if component is None or buffer is None:
        session.operations.reset_logs()
        return None
    duration = max(0.0, time.monotonic() - started_at) if started_at else 0.0
    path = str(buffer.full_log_path) if buffer.full_log_path is not None else "尚未生成"
    body = Text(
        f"持续 {duration:.1f} 秒 · 接收 {session.operations.received_lines} 行"
        f" · 警告 {session.operations.warning_lines} 行\n完整日志 {path}",
        style="dim",
    )
    session.operations.reset_logs()
    return _standalone_activity(
        f"已结束{service_display_name(component)}日志跟随", body
    )


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
