"""Runtime resource discovery and configuration interaction flow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
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
from ...catalog import SECTION_ACTIONS
from ...session import GuidedSession
from .views import RESOURCE_LABELS, record_summary
from .wizard import ResourceWizardState, save_resource_wizard
from .actions import (
    detail_actions,
    detail_renderable,
    execute_action,
    identity,
    list_records,
    preview_action,
    summary,
    summary_renderable,
)
from ...navigation import (
    action_id,
    context_items,
    context_label,
)
from ...operation import OperationSpec
from ...results import ResultKind, ResultRoute
from ...selection import (
    ResourceRecordView,
    SelectionRecord,
    selected_value,
    selection_records,
)


def context_title(session: GuidedSession, base: str) -> str:
    """Return the Resources-owned identity suffix for the shell chrome."""

    context = session.context
    if context in {
        ("resources", "account-operations"),
        ("resources", "account-orders"),
    }:
        account = _selected_account_id(session.resources.selected)
        return f"{base} · {account}" if account else base
    kind = session.resources.kind
    label = RESOURCE_LABELS.get(kind or "")
    if len(context) == 2 and context[1] in RESOURCE_LABELS:
        return f"首页 / 运行准备 / {RESOURCE_LABELS[context[1]]}"
    if context == ("resources", "selected") and label:
        rid = (
            identity(kind, session.resources.selected)
            if kind is not None and session.resources.selected is not None
            else ""
        )
        return f"{base} · {label}" + (f" · {rid}" if rid else "")
    if context == ("resources", "setup") and label:
        wizard = session.resources.wizard
        rid = ""
        if isinstance(wizard, ResourceWizardState):
            rid = (
                identity(wizard.kind, wizard.record)
                if wizard.record
                else str(wizard.answers.get("resource-id") or "新建")
            )
        return f"{base} · {label} · {rid or '新建'}"
    return base


def empty_resource_label(session: GuidedSession) -> str | None:
    context = session.context
    if (
        len(context) == 2
        and context[0] == "resources"
        and context[1] in RESOURCE_LABELS
        and not session.visible_records
    ):
        return RESOURCE_LABELS[context[1]]
    return None


def _selected_account_id(record: Mapping[str, Any] | None) -> str:
    if record is None:
        return ""
    return str(record.get("account_id") or record.get("id") or "")


def handle_input(
    state: Any, session: GuidedSession, token: ActionToken, value: str
) -> tuple[ScreenEffect, ...] | None:
    """Continue one typed Resources input interaction."""

    if token.feature is not Feature.RESOURCES:
        return None
    return handle_command(state, session, token.action, (value,))


def handle_command(
    state: Any, session: GuidedSession, command: str, arguments: tuple[str, ...]
) -> tuple[ScreenEffect, ...] | None:
    value = " ".join(arguments).strip()
    if command == "new" and session.context[:1] == ("resources",):
        if session.resources.kind is None:
            return _choice(
                state,
                session,
                Text("请先进入一种运行资源列表。", style="yellow"),
                "请选择资源类型",
            )
        return _start_wizard(
            state, session, ResourceWizardState(session.resources.kind)
        )
    if command.startswith("resource:setup-field:"):
        wizard = session.resources.wizard
        if not isinstance(wizard, ResourceWizardState):
            session.enter("resources")
            return _choice(
                state,
                session,
                Text("资源配置向导已经失效。", style="yellow"),
                "向导已失效",
            )
        try:
            name = command.removeprefix("resource:setup-field:")
            wizard.accept(name, value)
            if name == "notification-provider" and not wizard.editing:
                wizard.generated_id = _available_notification_id(
                    state, str(wizard.answers[name])
                )
        except ValueError as error:
            return _input_error(session, str(error))
        return _advance_wizard(state, session, wizard)
    if command == "resource:model-test":
        return _resource_confirmation(state, session, "test", value=value)
    if command == "resource:notification-mode":
        return (_resource_run(state, session, "validate", value=value or "paper"),)
    if command in {"resource:notification-attach", "resource:notification-detach"}:
        action = command.rsplit("-", 1)[-1]
        session.resources.action = action
        session.resources.launch_id = value
        if action == "attach":
            return _ask(
                session,
                "resource:notification-route",
                "请输入通知 route；直接回车使用 signals",
                "输入 /back 取消。",
            )
        return _resource_confirmation(state, session, "detach", launch_id=value)
    if command == "resource:notification-route":
        return _resource_confirmation(
            state,
            session,
            "attach",
            value=value or "signals",
            launch_id=session.resources.launch_id,
        )
    return None


def handle_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if session.context[:1] != ("resources",):
        return None
    if session.context == ("resources", "setup"):
        wizard = session.resources.wizard
        if not isinstance(wizard, ResourceWizardState):
            return None
        prompt = wizard.next_prompt()
        if prompt is None or prompt[0] != "notification-provider":
            return None
        provider = action_id(_NOTIFICATION_PROVIDER_ACTIONS, command)
        if provider is None:
            return None
        wizard.accept("notification-provider", provider)
        if not wizard.editing:
            wizard.generated_id = _available_notification_id(state, provider)
        return _advance_wizard(state, session, wizard)
    if session.context == ("resources", "selected"):
        kind, record = session.resources.kind, session.resources.selected
        if kind is None or record is None:
            session.enter("resources")
            return _choice(state, session)
        action = action_id(detail_actions(kind), command)
        if action is None:
            return None
        if action == "advanced":
            return (_resource_run(state, session, action),)
        if action == "models" and kind == "models":
            body = Panel(
                Pretty({"models": list(record.get("models") or ())}), title="已保存模型"
            )
            return _standalone(
                f"{identity(kind, record)} · 已保存模型", body
            ), *_choice(state, session)
        if action == "operations" and kind == "accounts":
            account = identity(kind, record)
            session.context = ("resources", "account-operations")
            body = Panel(
                f"已选择账户 {account}。可直接输入 account --account-id {account} standalone overview 等 kairos 业务命令。",
                title=f"账户运行查询 · {account}",
                border_style="cyan",
            )
            return _standalone(f"账户运行查询 · {account}", body), *_choice(
                state, session
            )
        if action == "edit":
            return _start_wizard(
                state, session, ResourceWizardState(kind, dict(record))
            )
        if action == "test" and kind == "models":
            return _ask(
                session,
                "resource:model-test",
                "请输入用于连接测试的模型 ID",
                "输入 /back 取消。",
            )
        if action == "validate" and kind == "notifications":
            return _ask(
                session,
                "resource:notification-mode",
                "请输入运行模式；直接回车使用 paper",
                "输入 /back 取消。",
            )
        if action in {"attach", "detach"} and kind == "notifications":
            return _ask(
                session,
                f"resource:notification-{action}",
                "请输入 Launch ID",
                "输入 /back 取消。",
            )
        if action in {"test", "toggle", "delete"}:
            return _resource_confirmation(state, session, action)
        return _choice(
            state, session, Text(f"{action} 尚不可用。", style="yellow"), "操作不可用"
        )
    if len(session.context) > 1 and session.visible_records:
        record = _record_choice(session.visible_records, command)
        if record is None or session.resources.kind is None:
            return None
        selected = ResourceRecordView.from_mapping(record)
        session.resources.selected = selected
        session.context = ("resources", "selected")
        body = detail_renderable(session.resources.kind, selected)
        return _standalone(
            f"{identity(session.resources.kind, selected)} · 资源详情", body
        ), *_choice(state, session)
    action = action_id(SECTION_ACTIONS["resources"], command)
    if action is None:
        return None
    if action == "check":
        return (
            _run(
                "resources.check",
                "检查运行资源",
                ResultRoute(ResultKind.RESOURCES_SUMMARY),
                lambda: summary(state),
            ),
        )
    session.resources.kind = action
    return (
        _run(
            f"resources.list.{action}",
            f"查看 {RESOURCE_LABELS[action]}",
            ResultRoute(ResultKind.RESOURCE_LIST, action),
            lambda: list_records(state, action),
        ),
    )


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    kind = spec.route.kind
    if kind is ResultKind.RESOURCE_LIST:
        resource_kind = spec.route.qualifier
        assert resource_kind is not None
        records = tuple(
            ResourceRecordView.from_mapping(record) for record in (result or ())
        )
        session.resources.kind = resource_kind
        session.context = ("resources", resource_kind)
        visible = selection_records(
            records,
            key=lambda record: identity(resource_kind, record),
            label=lambda record: identity(resource_kind, record),
            description=lambda record: record_summary(resource_kind, record),
        )
        session.visible_records = visible
        if records:
            actions = tuple(
                ActionItem(
                    str(i),
                    record.label,
                    record.description,
                    str(i),
                )
                for i, record in enumerate(visible, 1)
            )
            status = f"找到 {len(records)} 个结果 · 请选择"
        else:
            actions = (
                ActionItem(
                    "new",
                    f"添加{RESOURCE_LABELS[resource_kind]}",
                    "启动安全的单输入配置向导",
                    "new",
                ),
            )
            status = f"尚未配置 {RESOURCE_LABELS[resource_kind]}"
        interaction = ChoiceInteraction(title=_title(session), actions=actions)
        session.interaction = interaction
        return SetInteraction(interaction), SetStatus(status)
    if kind is ResultKind.RESOURCES_SUMMARY:
        body = summary_renderable(result)
        return _activity(spec, body), *_choice(state, session, status="检查已完成")
    if kind is ResultKind.RESOURCE_ACTION:
        action = spec.route.qualifier
        assert action is not None
        resource_kind, selected = session.resources.kind, session.resources.selected
        label = RESOURCE_LABELS.get(resource_kind or "", "运行资源")
        rid = identity(resource_kind, selected) if resource_kind and selected else ""
        body = Panel(
            Pretty(result, expand_all=True),
            title=f"{label} · {rid} · 资源操作结果"
            if rid
            else f"{label} · 资源操作结果",
        )
        if action == "delete":
            if selected is not None and resource_kind is not None:
                selected_id = identity(resource_kind, selected)
                session.visible_records = tuple(
                    record
                    for record in session.visible_records
                    if record.key != selected_id
                )
            session.resources.selected = None
            session.context = ("resources",)
        elif isinstance(result, Mapping) and any(
            key in result for key in ("account_id", "connection_id", "destination_id")
        ):
            session.resources.selected = ResourceRecordView.from_mapping(result)
        return _activity(spec, body), *_choice(state, session, status="资源操作已完成")
    if kind is ResultKind.RESOURCE_WIZARD:
        wizard = session.resources.wizard
        wizard_kind = wizard.kind if isinstance(wizard, ResourceWizardState) else None
        label = RESOURCE_LABELS.get(wizard_kind or "", "运行资源")
        rid = (
            identity(wizard_kind, result)
            if wizard_kind and isinstance(result, Mapping)
            else ""
        )
        if rid == "unknown" and isinstance(wizard, ResourceWizardState):
            rid = str(wizard.answers.get("resource-id") or "")
        body = Panel(
            Pretty(result, expand_all=True),
            title=f"{label} · {rid} · 配置结果" if rid else f"{label} · 配置结果",
        )
        if isinstance(wizard, ResourceWizardState):
            if isinstance(result, Mapping) and result.get("status") == "preview":
                session.resources.selected = None
                session.context = ("resources", wizard.kind)
            else:
                selected = (
                    ResourceRecordView.from_mapping(result)
                    if isinstance(result, Mapping)
                    else ResourceRecordView({})
                )
                session.resources.selected = selected
                session.context = ("resources", "selected")
            wizard.clear_secrets()
        session.resources.wizard = None
        return _activity(spec, body), *_choice(state, session, status="资源配置已完成")
    return None


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind not in _KINDS:
        return None
    if spec.route.kind is ResultKind.RESOURCE_WIZARD:
        _clear_wizard(session)
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
    if spec.route.kind is ResultKind.RESOURCE_WIZARD:
        _clear_wizard(session)
    session.clear_result_flow(spec.route.kind)
    body = Text("操作在开始执行后被取消。", style="yellow")
    return (
        _activity(spec, body, ActivityOutcome.CANCELLED),
        *_choice(state, session, status="操作已取消 · 可继续输入"),
    )


def cancel_input(session: GuidedSession, token: ActionToken) -> bool:
    command = token.action
    if command.startswith("resource:setup"):
        _clear_wizard(session)
        return True
    if command.startswith("resource:"):
        session.resources.action = None
        session.resources.launch_id = None
    return False


def back_wizard(
    state: Any, session: GuidedSession, token: ActionToken
) -> tuple[ScreenEffect, ...] | None:
    """Return a resource wizard to its previous field without discarding it."""

    if not token.action.startswith("resource:setup"):
        return None
    wizard = session.resources.wizard
    if not isinstance(wizard, ResourceWizardState) or not wizard.go_back():
        return None
    return _advance_wizard(state, session, wizard)


def _start_wizard(
    state: Any, session: GuidedSession, wizard: ResourceWizardState
) -> tuple[ScreenEffect, ...]:
    session.resources.wizard = wizard
    session.resources.kind = wizard.kind
    session.context = ("resources", "setup")
    return _advance_wizard(state, session, wizard)


def _advance_wizard(
    state: Any, session: GuidedSession, wizard: ResourceWizardState
) -> tuple[ScreenEffect, ...]:
    prompt = wizard.next_prompt()
    if prompt:
        name, label, detail, secret = prompt
        current, total = wizard.step_progress()
        if name == "notification-provider":
            interaction = ChoiceInteraction(
                title=f"{_title(session)} · 第 {current}/{total} 步",
                summary=_wizard_summary(wizard),
                actions=_NOTIFICATION_PROVIDER_ACTIONS,
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("请选择通知渠道")
        session.ask(
            ActionToken(Feature.RESOURCES, f"resource:setup-field:{name}"),
            title=f"{_title(session)} · 第 {current}/{total} 步",
            prompt=label,
            detail=detail,
            value_summary=_wizard_summary(wizard),
            secret=secret,
        )
        return SetInteraction(session.interaction), SetStatus("等待资源配置")

    def operation() -> Any:
        if state.dry_run or state.no_exec:
            return {
                "status": "preview",
                "action": "resource-save",
                "summary": wizard.redacted_summary(),
            }
        return save_resource_wizard(state, wizard)

    spec = _spec(
        "resources.save",
        f"保存{wizard.kind}运行资源",
        ResultRoute(ResultKind.RESOURCE_WIZARD),
        operation,
    )
    confirmation_title = "资源配置脱敏摘要"
    if wizard.kind == "notifications":
        _, total = wizard.step_progress()
        confirmation_title = f"确认通知提醒配置 · 第 {total}/{total} 步"
    return _confirm_or_run(
        state,
        session,
        spec,
        details=(
            _wizard_summary(wizard, final=True)
            if wizard.kind == "notifications"
            else Pretty(wizard.redacted_summary(), expand_all=True)
        ),
        title=confirmation_title,
    )


def _wizard_summary(wizard: ResourceWizardState, *, final: bool = False) -> Any:
    if wizard.kind != "notifications":
        return Pretty(wizard.redacted_summary(), expand_all=True)
    provider = str(wizard.answers.get("notification-provider") or "")
    provider_label = {"feishu": "飞书", "telegram": "Telegram"}.get(
        provider, "尚未选择"
    )
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("操作", "修改通知提醒" if wizard.editing else "创建通知提醒")
    table.add_row("渠道", provider_label)
    if provider:
        table.add_row(
            "凭据",
            "已填写"
            if wizard.answers.get("secret-primary")
            else ("沿用现有凭据" if wizard.editing else "待填写"),
        )
    if provider == "telegram":
        table.add_row("Chat ID", str(wizard.answers.get("chat-id") or "待填写"))
    if final:
        resource_id = (
            identity(wizard.kind, wizard.record)
            if wizard.record
            else wizard.generated_id or "自动生成"
        )
        table.add_row("通知名称", resource_id)
    elif not wizard.editing:
        table.add_row("通知名称", "系统自动生成")
    return table


def _available_notification_id(state: Any, provider: str) -> str:
    base = f"{provider}-alerts"
    existing = {
        identity("notifications", record)
        for record in list_records(state, "notifications")
    }
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


_NOTIFICATION_PROVIDER_ACTIONS = (
    ActionItem("feishu", "飞书（推荐）", "使用群机器人 Webhook", "1"),
    ActionItem("telegram", "Telegram", "使用机器人令牌和 Chat ID", "2"),
)


def _resource_run(
    state: Any,
    session: GuidedSession,
    action: str,
    *,
    value: str | None = None,
    launch_id: str | None = None,
) -> RunOperation:
    kind, record = session.resources.kind, session.resources.selected
    assert kind is not None and record is not None

    def operation() -> Any:
        if action in {"test", "toggle", "delete", "attach", "detach"} and (
            state.dry_run or state.no_exec
        ):
            return preview_action(
                kind, record, action, value=value, launch_id=launch_id
            )
        return execute_action(
            state, kind, record, action, value=value, launch_id=launch_id
        )

    return _run(
        f"resources.{kind}.{action}",
        f"kairos resource {action} {identity(kind, record)}",
        ResultRoute(ResultKind.RESOURCE_ACTION, action),
        operation,
    )


def _resource_confirmation(
    state: Any,
    session: GuidedSession,
    action: str,
    *,
    value: str | None = None,
    launch_id: str | None = None,
) -> tuple[ScreenEffect, ...]:
    run = _resource_run(state, session, action, value=value, launch_id=launch_id)
    session.resources.action = None
    session.resources.launch_id = None
    return _confirm_or_run(state, session, run.operation)


def _ask(
    session: GuidedSession,
    action: str,
    prompt: str,
    detail: str,
    summary: Any | None = None,
) -> tuple[ScreenEffect, ...]:
    session.ask(
        ActionToken(Feature.RESOURCES, action),
        title=_title(session),
        prompt=prompt,
        detail=detail,
        value_summary=summary,
    )
    return SetInteraction(session.interaction), SetStatus("等待输入")


def _input_error(session: GuidedSession, error: str) -> tuple[ScreenEffect, ...]:
    current = session.interaction
    if isinstance(current, InputInteraction):
        current = InputInteraction(
            current.action,
            current.title,
            current.prompt,
            current.detail,
            current.value_summary,
            current.secret,
            error,
        )
        session.interaction = current
        return SetInteraction(current), SetStatus("输入有误 · 请修正")
    return (SetStatus(error),)


def _confirm_or_run(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
    *,
    dangerous: bool = True,
    details: Any | None = None,
    title: str = "需要确认",
) -> tuple[ScreenEffect, ...]:
    if not dangerous or state.yes or state.dry_run or state.no_exec:
        return (RunOperation(spec),)
    session.confirm(spec, title=title, display_summary=details)
    return SetInteraction(session.interaction), SetStatus("等待确认")


def _run(
    action: str, summary: str, route: ResultRoute, operation: Callable[[], Any]
) -> RunOperation:
    return RunOperation(_spec(action, summary, route, operation))


def _spec(
    action: str, summary: str, route: ResultRoute, operation: Callable[[], Any]
) -> OperationSpec:
    return OperationSpec.create(
        action_name=action,
        audit_summary=summary,
        route=route,
        operation=operation,
        running_status="正在执行…",
    )


def _choice(
    state: Any, session: GuidedSession, summary: Any | None = None, status: str = "就绪"
) -> tuple[ScreenEffect, ...]:
    interaction = ChoiceInteraction(
        title=_title(session), summary=summary, actions=context_items(session, state)
    )
    session.interaction = interaction
    return SetInteraction(interaction), SetStatus(status)


def _title(session: GuidedSession) -> str:
    base = context_label(session.context)
    kind = session.resources.kind
    label = RESOURCE_LABELS.get(kind or "")
    if (
        len(session.context) == 2
        and session.context[0] == "resources"
        and session.context[1] in RESOURCE_LABELS
    ):
        return f"首页 / 运行准备 / {RESOURCE_LABELS[session.context[1]]}"
    if session.context in {
        ("resources", "account-operations"),
        ("resources", "account-orders"),
    }:
        account = _account_id(session.resources.selected)
        return f"{base} · {account}" if account else base
    if (
        session.context == ("resources", "selected")
        and label
        and kind is not None
        and session.resources.selected
    ):
        return f"{base} · {label} · {identity(kind, session.resources.selected)}"
    if session.context == ("resources", "setup") and label:
        wizard = session.resources.wizard
        rid = ""
        if isinstance(wizard, ResourceWizardState):
            rid = (
                identity(wizard.kind, wizard.record)
                if wizard.record
                else str(wizard.answers.get("resource-id") or "新建")
            )
        return f"{base} · {label} · {rid or '新建'}"
    return base


def _record_choice(records: tuple[SelectionRecord, ...], value: str) -> object | None:
    return selected_value(records, value)


def _account_id(record: Mapping[str, Any] | None) -> str:
    return str(record.get("account_id") or "").strip() if record else ""


def _clear_wizard(session: GuidedSession) -> None:
    wizard = session.resources.wizard
    session.resources.wizard = None
    if isinstance(wizard, ResourceWizardState):
        wizard.clear_secrets()
        session.context = (
            ("resources", "selected")
            if wizard.editing and session.resources.selected
            else ("resources", wizard.kind)
        )


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


def _standalone(title: str, body: Any) -> AppendActivity:
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
        ResultKind.RESOURCES_SUMMARY,
        ResultKind.RESOURCE_LIST,
        ResultKind.RESOURCE_ACTION,
        ResultKind.RESOURCE_WIZARD,
    }
)
__all__ = [
    "cancel_input",
    "handle_cancel",
    "handle_command",
    "handle_context",
    "handle_failure",
    "handle_success",
]
