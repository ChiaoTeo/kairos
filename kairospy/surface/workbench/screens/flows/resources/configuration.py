"""Runtime resource discovery and configuration interaction flow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

from rich.console import Group
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from kairospy.strategy.apps.agent.application import (
    AgentResourceApplication,
    ModelConnectionDraftApplication,
)
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)

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
from ...catalog import AI_MODEL_ACTIONS, SECTION_ACTIONS
from ...session import GuidedSession
from .views import (
    RESOURCE_LABELS,
    action_result_renderable,
    mapping_renderable,
    model_catalog_renderable,
    record_summary,
    records_renderable,
    saved_resource_renderable,
)
from .wizard import ResourceWizardState, prepare_model_draft, save_resource_wizard
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
        ("resources", "account-order-segments"),
        ("resources", "account-orders"),
    }:
        account = _selected_account_id(session.resources.selected)
        if context == ("resources", "account-order-segments"):
            root = f"{session.root_label} / 运行准备 / 订单管理"
            return f"{root} · {account} / 选择交易分区" if account else base
        title = f"{base} · {account}" if account else base
        segment = session.account.selected_segment
        if context == ("resources", "account-orders") and segment:
            title = f"{title} / {segment}"
        return title
    kind = session.resources.kind
    label = RESOURCE_LABELS.get(kind or "")
    if len(context) == 2 and context[1] in RESOURCE_LABELS:
        return f"{session.root_label} / 运行准备 / {RESOURCE_LABELS[context[1]]}"
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
    if command == "resource:model-manual":
        wizard = session.resources.wizard
        if not isinstance(wizard, ResourceWizardState) or wizard.kind != "models":
            return None
        model = value.strip()
        if (
            not model
            or len(model) > 256
            or any(character.isspace() for character in model)
        ):
            return _input_error(session, "模型 ID 不能为空或包含空白字符")
        wizard.discovered_models = (
            *wizard.discovered_models,
            {"id": model, "name": model, "source": "manual"},
        )
        return (_start_model_test(state, wizard, model),)
    if command == "resource:model-test":
        model = value.strip()
        if (
            not model
            or len(model) > 256
            or any(character.isspace() for character in model)
        ):
            return _input_error(session, "模型 ID 不能为空或包含空白字符")
        return _ask_model_message(session, model)
    if command == "resource:model-chat":
        if session.context != ("resources", "model-chat"):
            return None
        if not value:
            return (SetStatus("消息不能为空"),)
        record = session.resources.selected
        if record is None:
            return None
        model = identity("models", record)
        run = _model_conversation_run(state, session, model, value)
        return (
            _chat_activity("你", value, outcome=ActivityOutcome.NOTICE),
            run,
        )
    return None


def handle_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if session.context[:1] != ("resources",):
        return None
    if session.context == ("resources", "ai-models"):
        resource_kind = action_id(AI_MODEL_ACTIONS, command)
        if resource_kind is not None:
            return (
                _run(
                    f"resources.list.{resource_kind}",
                    f"查看 {RESOURCE_LABELS[resource_kind]}",
                    ResultRoute(ResultKind.RESOURCE_LIST, resource_kind),
                    lambda: list_records(state, resource_kind),
                ),
            )
    if session.context == ("resources", "setup"):
        wizard = session.resources.wizard
        if not isinstance(wizard, ResourceWizardState):
            return None
        if wizard.kind == "models" and wizard.model_phase == "model-choice":
            selected = action_id(_model_actions(wizard), command)
            if selected is None:
                return None
            if selected == "manual":
                return _ask(
                    session,
                    "resource:model-manual",
                    "请输入模型 ID",
                    "仅在模型目录没有返回目标模型时手动填写；输入 /back 返回。",
                    _wizard_summary(wizard),
                )
            model = _selected_discovered_model(wizard, selected)
            if model is None:
                return None
            return (_start_model_test(state, wizard, model),)
        prompt = wizard.next_prompt()
        if prompt is None or prompt[0] not in {
            "notification-provider",
            "model-provider",
            "model-mode",
            "account-mode",
            "account-provider",
            "account-role",
            "account-segment",
            "data-provider",
            "data-product",
            "credential-mode",
            "credential-id",
            "endpoint-id",
        }:
            return None
        field = prompt[0]
        actions = {
            "notification-provider": _NOTIFICATION_PROVIDER_ACTIONS,
            "model-provider": _model_provider_actions(wizard),
            "model-mode": _MODEL_MODE_ACTIONS,
            "account-mode": _ACCOUNT_MODE_ACTIONS,
            "account-provider": _ACCOUNT_PROVIDER_ACTIONS,
            "account-role": _ACCOUNT_ROLE_ACTIONS,
            "account-segment": _ACCOUNT_SEGMENT_ACTIONS,
            "data-provider": _DATA_PROVIDER_ACTIONS,
            "data-product": _data_product_actions(wizard),
            "credential-mode": _CREDENTIAL_MODE_ACTIONS,
            "credential-id": _credential_actions(wizard),
            "endpoint-id": _model_endpoint_actions(wizard),
        }[field]
        value = action_id(actions, command)
        if value is None:
            return None
        if field == "endpoint-id" and value == "__new_endpoint__":
            session.resources.parent_wizard = wizard
            return _start_wizard(state, session, ResourceWizardState("model_endpoints"))
        wizard.accept(field, value)
        if field == "notification-provider" and not wizard.editing:
            wizard.generated_id = _available_notification_id(state, value)
        return _advance_wizard(state, session, wizard)
    if session.context == ("resources", "selected"):
        kind, record = session.resources.kind, session.resources.selected
        if kind is None or record is None:
            session.enter("resources")
            return _choice(state, session)
        if kind == "accounts" and session.resources.action == "access-purpose":
            purpose = action_id(_ACCOUNT_ACCESS_ACTIONS, command)
            if purpose is None:
                return None
            credential_actions = _account_credential_actions(state, record)
            if not credential_actions:
                session.resources.action = None
                return _choice(
                    state,
                    session,
                    Text(
                        "尚无同 Provider 的凭据；请先通过账户编辑安全创建。",
                        style="yellow",
                    ),
                    "没有可用凭据",
                )
            session.resources.action = f"access-credential:{purpose}"
            interaction = ChoiceInteraction(
                title=f"{_title(session)} · 选择 {purpose} 凭据",
                summary=_account_access_summary(record, purpose),
                actions=credential_actions,
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("请选择账户访问凭据")
        if kind == "accounts" and str(session.resources.action or "").startswith(
            "access-credential:"
        ):
            purpose = str(session.resources.action).partition(":")[2]
            credential = action_id(_account_credential_actions(state, record), command)
            if credential is None:
                return None
            session.resources.action = None
            run = _resource_run(
                state,
                session,
                "access",
                value=f"{purpose}|{credential}",
            )
            return _confirm_or_run(
                state,
                session,
                run.operation,
                details=_account_access_summary(record, purpose, credential),
                title=(
                    "确认启用订单交易访问"
                    if purpose == "order-trade"
                    else "确认账户只读访问"
                ),
            )
        action = action_id(detail_actions(kind), command)
        if action is None:
            return None
        if action == "advanced":
            return (_resource_run(state, session, action),)
        if action == "operations" and kind == "accounts":
            session.context = ("resources", "account-operations")
            return _choice(state, session, status="已进入账户上下文")
        if action == "access" and kind == "accounts":
            session.resources.action = "access-purpose"
            interaction = ChoiceInteraction(
                title=f"{_title(session)} · 管理账户访问",
                summary=_account_access_summary(record, ""),
                actions=_ACCOUNT_ACCESS_ACTIONS,
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("请选择账户访问用途")
        if action == "edit":
            return _start_wizard(
                state, session, ResourceWizardState(kind, dict(record))
            )
        if action == "test" and kind == "models":
            model_id = identity(kind, record)
            session.context = ("resources", "model-chat")
            session.resources.action = "chat"
            interaction = ChoiceInteraction(title="", summary=None, actions=())
            session.interaction = interaction
            return (
                _chat_activity(
                    "系统",
                    f"已进入 {model_id} 对话。直接输入消息；/back 返回模型操作。",
                    outcome=ActivityOutcome.NOTICE,
                ),
                SetInteraction(interaction),
                SetStatus(f"正在与 {model_id} 对话"),
            )
        if action == "discover" and kind == "model_endpoints":
            return (_resource_run(state, session, action),)
        if action in {"test", "toggle", "delete"}:
            return _resource_confirmation(state, session, action)
        return _choice(
            state, session, Text(f"{action} 尚不可用。", style="yellow"), "操作不可用"
        )
    if len(session.context) > 1 and session.visible_records:
        record = _record_choice(session.visible_records, command)
        if not isinstance(record, Mapping) or session.resources.kind is None:
            return None
        selected = ResourceRecordView.from_mapping(record)
        session.resources.selected = selected
        session.resources.action = None
        if session.resources.kind == "accounts":
            session.context = ("resources", "account-operations")
            return _choice(state, session, status="已进入账户上下文")
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
    if action == "models":
        session.resources.kind = None
        session.resources.selected = None
        session.visible_records = ()
        session.context = ("resources", "ai-models")
        interaction = ChoiceInteraction(
            title=_title(session), summary=None, actions=AI_MODEL_ACTIONS
        )
        session.interaction = interaction
        return SetInteraction(interaction), SetStatus("请选择模型资源")
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
    if kind is ResultKind.RESOURCE_WIZARD and spec.route.qualifier == "model-discover":
        return _handle_model_discovery_success(state, session, spec, result)
    if kind is ResultKind.RESOURCE_WIZARD and spec.route.qualifier == "model-test":
        return _handle_model_test_success(state, session, spec, result)
    if kind is ResultKind.RESOURCE_LIST:
        resource_kind = spec.route.qualifier
        assert resource_kind is not None
        records = tuple(
            ResourceRecordView.from_mapping(record) for record in (result or ())
        )
        return _enter_resource_list(session, resource_kind, records)
    if kind is ResultKind.RESOURCES_SUMMARY:
        body = summary_renderable(result)
        return _activity(spec, body), *_choice(state, session, status="检查已完成")
    if kind is ResultKind.RESOURCE_ACTION:
        action = spec.route.qualifier
        assert action is not None
        if action == "model-chat" and isinstance(result, Mapping):
            model_id = str(result.get("model_id") or "模型")
            succeeded = result.get("succeeded") is True
            if succeeded:
                response = str(result.get("response") or result.get("detail") or "")
            else:
                response = _model_chat_failure_message(result)
            interaction = ChoiceInteraction(title="", summary=None, actions=())
            session.interaction = interaction
            return (
                _chat_activity(
                    model_id,
                    response,
                    outcome=(
                        ActivityOutcome.SUCCESS
                        if succeeded
                        else ActivityOutcome.FAILURE
                    ),
                ),
                SetInteraction(interaction),
                SetStatus(
                    f"正在与 {model_id} 对话"
                    if succeeded
                    else "模型调用失败 · 可继续重试或 /back 返回"
                ),
            )
        resource_kind, selected = session.resources.kind, session.resources.selected
        label = RESOURCE_LABELS.get(resource_kind or "", "运行资源")
        rid = identity(resource_kind, selected) if resource_kind and selected else ""
        title = f"{label} · {rid} · 资源操作结果" if rid else f"{label} · 资源操作结果"
        body = action_result_renderable(
            resource_kind,
            action,
            result,
            title=title,
        )
        outcome = _resource_action_outcome(action, result)
        if action == "delete":
            session.resources.selected = None
            if resource_kind is not None:
                records = tuple(
                    ResourceRecordView.from_mapping(record)
                    for record in list_records(state, resource_kind)
                )
                return (
                    _activity(spec, body, outcome),
                    *_enter_resource_list(
                        session,
                        resource_kind,
                        records,
                        status="连接已删除 · 请选择其他连接",
                    ),
                )
            session.context = ("resources",)
        elif isinstance(result, Mapping) and any(
            key in result for key in ("account_id", "connection_id", "destination_id")
        ):
            session.resources.selected = ResourceRecordView.from_mapping(result)
        status = (
            "连接验证失败 · 请检查结果"
            if outcome is ActivityOutcome.FAILURE
            else "资源操作已完成"
        )
        return _activity(spec, body, outcome), *_choice(state, session, status=status)
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
        title = f"{label} · {rid} · 配置结果" if rid else f"{label} · 配置结果"
        body = (
            saved_resource_renderable(wizard_kind, result, title=title)
            if wizard_kind is not None and isinstance(result, Mapping)
            else Panel(str(result), title=title)
        )
        created_endpoint_id = (
            str(result.get("endpoint_id") or wizard.answers.get("resource-id") or "")
            if isinstance(result, Mapping) and isinstance(wizard, ResourceWizardState)
            else ""
        )
        if isinstance(wizard, ResourceWizardState):
            resource_kind = wizard.kind
            wizard.clear_secrets()
        else:
            resource_kind = None
        session.resources.wizard = None
        parent = session.resources.parent_wizard
        if (
            resource_kind == "model_endpoints"
            and isinstance(parent, ResourceWizardState)
            and created_endpoint_id
            and state.owner is not None
        ):
            session.resources.parent_wizard = None
            parent.model_endpoints = AgentResourceApplication(
                state.owner
            ).model_endpoints()
            parent.accept("endpoint-id", created_endpoint_id)
            return _activity(spec, body), *_start_wizard(state, session, parent)
        if resource_kind is not None:
            records = tuple(
                ResourceRecordView.from_mapping(record)
                for record in list_records(state, resource_kind)
            )
            return (
                _activity(spec, body),
                *_enter_resource_list(
                    session,
                    resource_kind,
                    records,
                    status=(
                        "已保存 · 选择模型开始验证"
                        if resource_kind == "models"
                        else "资源配置已完成 · 请选择连接"
                    ),
                ),
            )
        return _activity(spec, body), *_choice(state, session, status="资源配置已完成")
    return None


def _enter_resource_list(
    session: GuidedSession,
    resource_kind: str,
    records: tuple[ResourceRecordView, ...],
    *,
    status: str | None = None,
) -> tuple[ScreenEffect, ...]:
    """Enter one resource list and expose selection before management actions."""

    session.resources.kind = resource_kind
    session.resources.selected = None
    session.resources.action = None
    session.context = ("resources", resource_kind)
    visible = selection_records(
        records,
        key=lambda record: identity(resource_kind, record),
        label=lambda record: identity(resource_kind, record),
        description=lambda record: record_summary(resource_kind, record),
    )
    session.visible_records = visible
    if records:
        actions = (
            *tuple(
                ActionItem(
                    str(index),
                    record.label,
                    record.description,
                    str(index),
                )
                for index, record in enumerate(visible, 1)
            ),
            ActionItem(
                "new",
                f"添加{RESOURCE_LABELS[resource_kind]}",
                "启动安全的单输入配置向导",
                "n",
            ),
        )
        resolved_status = status or f"找到 {len(records)} 个结果 · 请选择"
    else:
        actions = (
            ActionItem(
                "new",
                f"添加{RESOURCE_LABELS[resource_kind]}",
                "启动安全的单输入配置向导",
                "new",
            ),
        )
        resolved_status = status or f"尚未配置 {RESOURCE_LABELS[resource_kind]}"
    interaction = ChoiceInteraction(
        title=_title(session),
        summary=(
            None
            if resource_kind in {"accounts", "models"} or not records
            else records_renderable(
                resource_kind, tuple(dict(record) for record in records)
            )
        ),
        actions=actions,
    )
    session.interaction = interaction
    return SetInteraction(interaction), SetStatus(resolved_status)


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind not in _KINDS:
        return None
    if (
        spec.route.kind is ResultKind.RESOURCE_ACTION
        and spec.route.qualifier == "model-chat"
        and session.context == ("resources", "model-chat")
    ):
        interaction = ChoiceInteraction(title="", summary=None, actions=())
        session.interaction = interaction
        return (
            _chat_activity("错误", error, outcome=ActivityOutcome.FAILURE),
            SetInteraction(interaction),
            SetStatus("模型调用失败 · 可继续重试或 /back 返回"),
        )
    if spec.route.kind is ResultKind.RESOURCE_WIZARD and spec.route.qualifier in {
        "model-discover",
        "model-test",
    }:
        wizard = session.resources.wizard
        if isinstance(wizard, ResourceWizardState):
            wizard.discard_model_draft()
            wizard.model_phase = "model-choice"
            interaction = _model_choice_interaction(
                session,
                wizard,
                error="模型发现失败，可手动输入模型 ID。"
                if spec.route.qualifier == "model-discover"
                else "模型测试失败，请选择其他模型或重试。",
            )
            session.interaction = interaction
            return (
                _activity(spec, Text(error, style="red"), ActivityOutcome.FAILURE),
                SetInteraction(interaction),
                SetStatus("模型连接尚未保存 · 可调整后重试"),
            )
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
    if spec.route.kind is ResultKind.RESOURCE_WIZARD and spec.route.qualifier in {
        "model-discover",
        "model-test",
    }:
        wizard = session.resources.wizard
        if isinstance(wizard, ResourceWizardState):
            wizard.discard_model_draft()
            wizard.model_phase = "model-choice"
            interaction = _model_choice_interaction(
                session, wizard, error="操作已取消，模型连接草稿仍可继续修改。"
            )
            session.interaction = interaction
            return (
                _activity(
                    spec,
                    Text("模型操作已取消。", style="yellow"),
                    ActivityOutcome.CANCELLED,
                ),
                SetInteraction(interaction),
                SetStatus("操作已取消 · 模型连接尚未保存"),
            )
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
    return False


def back_wizard(
    state: Any, session: GuidedSession, token: ActionToken
) -> tuple[ScreenEffect, ...] | None:
    """Return a resource wizard to its previous field without discarding it."""

    if not token.action.startswith("resource:setup"):
        return None
    wizard = session.resources.wizard
    if not isinstance(wizard, ResourceWizardState):
        return None
    if not wizard.go_back():
        parent = session.resources.parent_wizard
        if not isinstance(parent, ResourceWizardState):
            return None
        wizard.clear_secrets()
        session.resources.parent_wizard = None
        return _start_wizard(state, session, parent)
    return _advance_wizard(state, session, wizard)


def _start_wizard(
    state: Any, session: GuidedSession, wizard: ResourceWizardState
) -> tuple[ScreenEffect, ...]:
    if wizard.kind == "model_endpoints" and state.owner is not None:
        wizard.model_providers = tuple(
            dict(value)
            for value in AgentResourceApplication(state.owner).provider_catalog()
        )
    if state.owner is not None and wizard.kind in {"accounts", "data"}:
        try:
            configured_credentials = CredentialConfigurationApplication(
                state.owner
            ).list()
        except (AttributeError, OSError, ValueError):
            configured_credentials = []
        wizard.credentials = tuple(dict(value) for value in configured_credentials)
    if state.owner is not None and wizard.kind == "models":
        try:
            wizard.model_endpoints = AgentResourceApplication(
                state.owner
            ).model_endpoints()
        except (AttributeError, OSError, ValueError):
            wizard.model_endpoints = ()
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
        choice_actions = {
            "notification-provider": _NOTIFICATION_PROVIDER_ACTIONS,
            "model-provider": _model_provider_actions(wizard),
            "model-mode": _MODEL_MODE_ACTIONS,
            "account-mode": _ACCOUNT_MODE_ACTIONS,
            "account-provider": _ACCOUNT_PROVIDER_ACTIONS,
            "account-role": _ACCOUNT_ROLE_ACTIONS,
            "account-segment": _ACCOUNT_SEGMENT_ACTIONS,
            "data-provider": _DATA_PROVIDER_ACTIONS,
            "data-product": _data_product_actions(wizard),
            "credential-mode": _CREDENTIAL_MODE_ACTIONS,
            "credential-id": _credential_actions(wizard),
            "endpoint-id": _model_endpoint_actions(wizard),
        }.get(name)
        if choice_actions is not None:
            title = f"{_title(session)} · 第 {current}/{total} 步"
            choice_summary = _wizard_summary(wizard)
            if name == "endpoint-id" and not wizard.model_endpoints:
                title = f"{_title(session)} · 准备模型服务"
                choice_summary = Group(
                    Text("还没有配置模型服务", style="bold yellow"),
                    Text(
                        "使用模型前，需要先连接 OpenAI、Anthropic、Ollama "
                        "或其他模型服务。配置完成后会自动返回这里。",
                        style="dim",
                    ),
                    Text(),
                    choice_summary,
                )
            interaction = ChoiceInteraction(
                title=title,
                summary=choice_summary,
                actions=choice_actions,
            )
            session.interaction = interaction
            status = {
                "notification-provider": "请选择通知渠道",
                "model-provider": "请选择模型服务",
                "model-mode": "请选择接口协议",
                "account-mode": "请选择账户类型",
                "account-provider": "请选择交易服务商",
                "account-role": "请选择账户权限",
                "account-segment": "请选择交易产品",
                "data-provider": "请选择行情服务商",
                "data-product": "请选择行情产品",
                "credential-mode": "请选择凭据方式",
                "credential-id": "请选择已有凭据",
                "endpoint-id": (
                    "请先配置模型服务"
                    if not wizard.model_endpoints
                    else "请选择模型服务，或添加另一个服务"
                ),
            }[name]
            return SetInteraction(interaction), SetStatus(status)
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
        "保存"
        + RESOURCE_LABELS.get(wizard.kind, wizard.kind)
        + (
            f" {identity(wizard.kind, wizard.record)}"
            if wizard.editing
            else f" {wizard.answers.get('resource-id', '')}".rstrip()
        ),
        ResultRoute(ResultKind.RESOURCE_WIZARD),
        operation,
    )
    confirmation_title = "资源配置脱敏摘要"
    if wizard.kind == "notifications":
        _, total = wizard.step_progress()
        confirmation_title = f"确认通知提醒配置 · 第 {total}/{total} 步"
    elif wizard.kind == "accounts":
        _, total = wizard.step_progress()
        confirmation_title = f"确认交易账户配置 · 第 {total}/{total} 步"
    return _confirm_or_run(
        state,
        session,
        spec,
        details=(
            _wizard_summary(wizard, final=True)
            if wizard.kind
            in {"accounts", "data", "notifications", "models", "model_endpoints"}
            else Pretty(wizard.redacted_summary(), expand_all=True)
        ),
        title=confirmation_title,
    )


def _wizard_summary(wizard: ResourceWizardState, *, final: bool = False) -> Any:
    if wizard.kind == "accounts":
        return _account_wizard_summary(wizard, final=final)
    if wizard.kind == "data":
        return _data_wizard_summary(wizard, final=final)
    if wizard.kind in {"models", "model_endpoints"}:
        return _model_wizard_summary(wizard, final=final)
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


def _data_wizard_summary(wizard: ResourceWizardState, *, final: bool = False) -> Table:
    provider = str(
        wizard.answers.get("data-provider") or wizard.record.get("provider") or ""
    )
    product = str(
        wizard.answers.get("data-product")
        or next(
            (
                value
                for value in wizard.record.get("products") or ()
                if value != "reference"
            ),
            "",
        )
    )
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row(
        "操作",
        "修改 Provider Connection" if wizard.editing else "创建 Provider Connection",
    )
    table.add_row("Provider", provider or "尚未选择")
    if product:
        table.add_row("产品", product)
    if "endpoint" in wizard.answers or final:
        table.add_row(
            "Endpoint",
            str(
                wizard.answers.get("endpoint")
                or wizard.record.get("endpoint")
                or "待填写"
            ),
        )
    mode = str(wizard.answers.get("credential-mode") or "")
    if mode:
        credential = (
            str(wizard.answers.get("credential-id") or "待选择")
            if mode == "existing"
            else "安全创建新凭据"
        )
        table.add_row("凭据", credential)
    if final:
        connection_id = (
            identity(wizard.kind, wizard.record)
            if wizard.editing
            else str(wizard.answers.get("resource-id") or "自动生成")
        )
        table.add_row("连接名称", connection_id)
        table.add_row("用途", "Reference/Market" if provider == "massive" else "Market")
    return table


def _account_wizard_summary(
    wizard: ResourceWizardState, *, final: bool = False
) -> Table:
    mode = str(
        wizard.answers.get("account-mode") or wizard.record.get("environment") or ""
    )
    provider = str(
        wizard.answers.get("account-provider") or wizard.record.get("broker") or ""
    )
    role = str(
        wizard.answers.get("account-role")
        or wizard.record.get("credential_role")
        or "readonly"
    )
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("操作", "修改交易账户" if wizard.editing else "创建交易账户")
    table.add_row(
        "账户类型",
        {"paper": "模拟账户", "live": "实盘账户"}.get(mode, "尚未选择"),
    )
    if mode == "live" and provider:
        table.add_row(
            "交易服务商",
            {"binance": "Binance", "okx": "OKX"}.get(provider, provider),
        )
        table.add_row(
            "权限",
            "只读（不下单、不转账）" if role == "readonly" else "交易",
        )
    if (
        "resource-id" in wizard.answers
        or wizard.editing
        or final
        or (mode == "live" and provider)
    ):
        account_id = (
            identity(wizard.kind, wizard.record)
            if wizard.editing
            else str(
                wizard.answers.get("resource-id") or wizard._default("resource-id")
            )
        )
        table.add_row("账户名称", account_id)
    if "account-segment" in wizard.answers:
        segment = str(wizard.answers["account-segment"])
        table.add_row(
            "交易产品", {"spot": "现货", "perpetual": "永续合约"}.get(segment, segment)
        )
    if mode == "live" and ("secret-primary" in wizard.answers or final):
        credential = (
            "已填写"
            if wizard.answers.get("secret-primary")
            else ("沿用现有凭据" if wizard.editing else "待填写")
        )
        table.add_row("API 凭据", credential)
    return table


def _model_wizard_summary(wizard: ResourceWizardState, *, final: bool = False) -> Table:
    if wizard.kind == "models":
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim")
        table.add_column()
        table.add_row("操作", "修改可用模型" if wizard.editing else "添加可用模型")
        table.add_row(
            "模型名称",
            identity("models", wizard.record)
            if wizard.editing
            else str(wizard.answers.get("resource-id") or "待填写"),
        )
        table.add_row(
            "模型服务",
            str(
                wizard.answers.get("endpoint-id")
                or wizard.record.get("endpoint_id")
                or "待选择"
            ),
        )
        table.add_row(
            "服务商模型 ID",
            str(
                wizard.answers.get("provider-model")
                or wizard.record.get("provider_model")
                or "待填写"
            ),
        )
        if final:
            table.add_row("初始状态", "待验证；保存后可执行对话测试")
        return table
    provider = (
        wizard.model_provider_label()
        if "model-provider" in wizard.answers or wizard.editing
        else "尚未选择"
    )
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("操作", "修改模型服务" if wizard.editing else "配置模型服务")
    table.add_row("模型服务", provider)
    if "resource-id" in wizard.answers or wizard.editing:
        connection_id = (
            identity(wizard.kind, wizard.record)
            if wizard.editing
            else str(wizard.answers.get("resource-id") or "待填写")
        )
        table.add_row("模型服务名称", connection_id)
    if "model-mode" in wizard.answers or (
        "model-provider" in wizard.answers and wizard.model_provider() != "custom"
    ):
        table.add_row(
            "接口协议",
            str(wizard.answers.get("model-mode") or wizard._default("model-mode")),
        )
    if "endpoint" in wizard.answers or (
        "model-provider" in wizard.answers and wizard.model_provider() != "custom"
    ):
        table.add_row(
            "API 地址",
            str(wizard.answers.get("endpoint") or wizard._default("endpoint")),
        )
        if "endpoint" in wizard.answers:
            table.add_row(
                "地址来源",
                "服务默认地址" if wizard.endpoint_defaulted else "用户配置",
            )
    if wizard.model_auth_required() and ("secret-primary" in wizard.answers or final):
        credential = (
            "已填写"
            if wizard.answers.get("secret-primary")
            else ("沿用现有凭据" if wizard.editing else "待填写")
        )
        table.add_row("API Key", credential)
    elif not wizard.model_auth_required() and "model-provider" in wizard.answers:
        table.add_row("认证", "本地连接，无需 API Key")
    return table


def _model_actions(wizard: ResourceWizardState) -> tuple[ActionItem, ...]:
    actions = tuple(
        ActionItem(
            f"model-{index}",
            str(value.get("name") or value.get("id") or "unknown"),
            str(value.get("id") or ""),
            str(index),
        )
        for index, value in enumerate(wizard.discovered_models, 1)
    )
    return (
        *actions,
        ActionItem(
            "manual",
            "手动输入模型 ID",
            "模型目录未返回时使用",
            str(len(actions) + 1),
        ),
    )


def _selected_discovered_model(wizard: ResourceWizardState, action: str) -> str | None:
    if not action.startswith("model-"):
        return None
    try:
        index = int(action.removeprefix("model-")) - 1
        value = wizard.discovered_models[index]
    except (ValueError, IndexError):
        return None
    return str(value.get("id") or "") or None


def _saved_model_actions(record: Mapping[str, Any]) -> tuple[ActionItem, ...]:
    models = tuple(dict.fromkeys(map(str, record.get("models") or ())))
    actions = tuple(
        ActionItem(f"saved-model-{index}", model, "发送一条测试消息", str(index))
        for index, model in enumerate(models, 1)
    )
    return (
        *actions,
        ActionItem(
            "manual",
            "手动输入模型 ID",
            "已知模型中没有目标模型时使用",
            str(len(actions) + 1),
        ),
    )


def _selected_saved_model(record: Mapping[str, Any], action: str) -> str | None:
    if not action.startswith("saved-model-"):
        return None
    try:
        index = int(action.removeprefix("saved-model-")) - 1
        return tuple(dict.fromkeys(map(str, record.get("models") or ())))[index]
    except (ValueError, IndexError):
        return None


def _ask_model_message(session: GuidedSession, model: str) -> tuple[ScreenEffect, ...]:
    session.resources.action = f"test-model-message:{model}"
    return _ask(
        session,
        "resource:model-chat",
        "请输入一条测试消息",
        "消息会真实发送给所选模型；输入 /back 取消。",
        Text(f"模型：{model}"),
    )


def _model_conversation_run(
    state: Any, session: GuidedSession, model: str, message: str
) -> RunOperation:
    record = session.resources.selected
    assert record is not None
    model_id = identity("models", record)

    def converse() -> Any:
        if state.dry_run or state.no_exec:
            return {
                "status": "preview",
                "succeeded": True,
                "model_id": model_id,
                "message": message,
                "response": "预览模式未执行真实模型调用",
            }
        return AgentResourceApplication(
            _workspace_owner(state)
        ).converse_with_available_model(model_id, message)

    return _run(
        "resources.models.converse",
        f"与模型对话 {model_id}",
        ResultRoute(ResultKind.RESOURCE_ACTION, "model-chat"),
        converse,
    )


def _model_choice_interaction(
    session: GuidedSession,
    wizard: ResourceWizardState,
    *,
    error: str | None = None,
) -> ChoiceInteraction:
    summary: Any = _wizard_summary(wizard)
    if error:
        summary = Group(summary, Text(), Text(error, style="bold red"))
    return ChoiceInteraction(
        title=f"{_title(session)} · 选择验证模型",
        summary=summary,
        actions=_model_actions(wizard),
    )


def _start_model_test(
    state: Any, wizard: ResourceWizardState, model: str
) -> RunOperation:
    wizard.discard_model_draft()
    wizard.selected_model = model
    wizard.model_phase = "testing"
    known_models = tuple(
        dict.fromkeys(
            str(value.get("id") or "")
            for value in wizard.discovered_models
            if value.get("id")
        )
    )
    if model not in known_models:
        known_models = (*known_models, model)

    def test_model() -> Any:
        if state.dry_run or state.no_exec:
            return {
                "draft": None,
                "model": model,
                "probe": {
                    "succeeded": True,
                    "detail": "预览模式未执行真实模型调用",
                    "error_category": None,
                },
                "preview": True,
            }
        draft = prepare_model_draft(state, wizard, models=known_models)
        probe = ModelConnectionDraftApplication(_workspace_owner(state)).test(
            draft, model
        )
        return {"draft": draft, "model": model, "probe": probe, "preview": False}

    return _run(
        "resources.models.test-draft",
        f"测试模型连接 {model}",
        ResultRoute(ResultKind.RESOURCE_WIZARD, "model-test"),
        test_model,
    )


def _handle_model_discovery_success(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
    result: Any,
) -> tuple[ScreenEffect, ...]:
    wizard = session.resources.wizard
    if not isinstance(wizard, ResourceWizardState) or not isinstance(result, Mapping):
        return _choice(state, session, Text("模型连接向导已经失效。", style="red"))
    wizard.model_draft = result.get("draft")
    wizard.discovered_models = tuple(
        dict(value)
        for value in result.get("models") or ()
        if isinstance(value, Mapping)
    )
    wizard.model_phase = "model-choice"
    interaction = _model_choice_interaction(session, wizard)
    session.interaction = interaction
    count = len(wizard.discovered_models)
    discovery_error = str(result.get("discovery_error") or "")
    if discovery_error:
        body = Text(
            f"模型目录不可用（{discovery_error}）。这不代表推理接口不可用，"
            "请手动输入模型 ID 继续测试。",
            style="yellow",
        )
        status = "模型目录不可用 · 请选择手动输入模型 ID"
    else:
        body = Text(
            f"发现 {count} 个模型。" if count else "未发现模型，可手动输入模型 ID。"
        )
        status = "请选择用于验证的模型"
    return (
        _activity(spec, body),
        SetInteraction(interaction),
        SetStatus(status),
    )


def _handle_model_test_success(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
    result: Any,
) -> tuple[ScreenEffect, ...]:
    wizard = session.resources.wizard
    if not isinstance(wizard, ResourceWizardState) or not isinstance(result, Mapping):
        return _choice(state, session, Text("模型连接向导已经失效。", style="red"))
    wizard.model_draft = result.get("draft")
    wizard.selected_model = str(result.get("model") or wizard.selected_model or "")
    probe = result.get("probe")
    if not isinstance(probe, Mapping) or probe.get("succeeded") is not True:
        wizard.model_phase = "model-choice"
        interaction = _model_choice_interaction(
            session,
            wizard,
            error=str(probe.get("detail") or "最小文本调用失败")
            if isinstance(probe, Mapping)
            else "最小文本调用失败",
        )
        session.interaction = interaction
        return (
            _activity(
                spec,
                action_result_renderable(
                    "models", "test", probe or {}, title="模型连接测试"
                ),
                ActivityOutcome.FAILURE,
            ),
            SetInteraction(interaction),
            SetStatus("模型测试失败 · 可选择模型后重试"),
        )
    wizard.model_phase = "ready"

    def commit() -> Any:
        if state.dry_run or state.no_exec:
            return {
                "status": "preview",
                "action": "model-connection-commit",
                "connection_id": str(
                    wizard.record.get("connection_id")
                    or wizard.answers.get("resource-id")
                    or ""
                ),
                "model_ref": f"{wizard.record.get('connection_id') or wizard.answers.get('resource-id')}/{wizard.selected_model}",
            }
        return save_resource_wizard(state, wizard)

    commit_spec = _spec(
        "resources.models.commit",
        "保存已验证模型连接",
        ResultRoute(ResultKind.RESOURCE_WIZARD, "model-commit"),
        commit,
    )
    confirmation = _confirm_or_run(
        state,
        session,
        commit_spec,
        details=_wizard_summary(wizard, final=True),
        title="确认模型连接",
    )
    return (
        _activity(
            spec,
            action_result_renderable("models", "test", probe, title="模型连接测试"),
        ),
        *confirmation,
    )


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


def _workspace_owner(state: Any) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    return state.owner


_NOTIFICATION_PROVIDER_ACTIONS = (
    ActionItem("feishu", "飞书（推荐）", "使用群机器人 Webhook", "1"),
    ActionItem("telegram", "Telegram", "使用机器人令牌和 Chat ID", "2"),
)

_ACCOUNT_MODE_ACTIONS = (
    ActionItem("paper", "模拟账户", "使用本地余额，不连接交易所", "1"),
    ActionItem("live", "交易所账户", "连接 Binance 或 OKX 的真实账户", "2"),
)

_ACCOUNT_PROVIDER_ACTIONS = (
    ActionItem("binance", "Binance", "连接 Binance API", "1"),
    ActionItem("okx", "OKX（原 OKEx）", "连接 OKX API", "2"),
)

_ACCOUNT_ROLE_ACTIONS = (
    ActionItem(
        "readonly",
        "只读（推荐）",
        "只读取账户、余额和持仓，不下单、不转账",
        "1",
    ),
    ActionItem("trade", "交易", "允许读取并执行订单；需要交易权限", "2"),
)

_ACCOUNT_ACCESS_ACTIONS = (
    ActionItem(
        "account-read",
        "账户只读访问",
        "读取身份、余额、持仓和账户事件，不允许下单",
        "1",
    ),
    ActionItem(
        "order-trade",
        "订单交易访问",
        "允许订单查询、提交、修改和撤销；不允许资金转移",
        "2",
    ),
)

_ACCOUNT_SEGMENT_ACTIONS = (
    ActionItem("spot", "现货", "读取现货账户余额和持仓", "1"),
    ActionItem("perpetual", "永续合约", "读取永续合约账户和持仓", "2"),
)

_DATA_PROVIDER_ACTIONS = (
    ActionItem("massive", "Massive", "美股、期权目录与行情", "1"),
    ActionItem("binance", "Binance", "现货、合约或鉴权行情", "2"),
    ActionItem("okx", "OKX（原 OKEx）", "现货、永续、期货或期权行情", "3"),
)

_CREDENTIAL_MODE_ACTIONS = (
    ActionItem("existing", "选择已有凭据", "复用同 Provider 的 Workspace 凭据", "1"),
    ActionItem("new", "安全创建新凭据", "在隐藏输入中填写认证字段", "2"),
)


def _data_product_actions(wizard: ResourceWizardState) -> tuple[ActionItem, ...]:
    provider = str(
        wizard.answers.get("data-provider")
        or wizard.record.get("provider")
        or "massive"
    )
    products = {
        "massive": (
            ("equity", "美股", "Reference 目录与美股行情"),
            ("options", "美股期权", "Reference 目录与期权行情"),
        ),
        "binance": (
            ("spot", "现货", "现货行情查询与订阅"),
            ("equity", "股票", "需要鉴权的股票行情"),
            ("usd-m-futures", "U 本位合约", "U 本位合约行情"),
            ("coin-m-futures", "币本位合约", "币本位合约行情"),
        ),
        "okx": (
            ("spot", "现货", "现货行情查询与订阅"),
            ("swap", "永续合约", "永续合约行情查询与订阅"),
            ("futures", "交割合约", "交割合约行情查询与订阅"),
            ("options", "期权", "期权行情查询与订阅"),
        ),
    }.get(provider, ())
    return tuple(
        ActionItem(product, label, description, str(index))
        for index, (product, label, description) in enumerate(products, 1)
    )


def _credential_actions(wizard: ResourceWizardState) -> tuple[ActionItem, ...]:
    provider = str(
        wizard.answers.get("data-provider")
        or wizard.answers.get("account-provider")
        or wizard.record.get("provider")
        or wizard.record.get("broker")
        or ""
    )
    values = [
        value for value in wizard.credentials if value.get("provider") == provider
    ]
    return tuple(
        ActionItem(
            str(value["credential_id"]),
            str(value["credential_id"]),
            "已配置 · Secret 不会显示",
            str(index),
        )
        for index, value in enumerate(values, 1)
    )


def _account_credential_actions(
    state: Any, record: Mapping[str, object]
) -> tuple[ActionItem, ...]:
    provider = str(record.get("integration_provider") or record.get("broker") or "")
    if state.owner is None:
        return ()
    try:
        credentials = CredentialConfigurationApplication(state.owner).list()
    except (AttributeError, OSError, ValueError):
        return ()
    return tuple(
        ActionItem(
            str(value["credential_id"]),
            str(value["credential_id"]),
            f"{provider} · Secret 不会显示",
            str(index),
        )
        for index, value in enumerate(
            (item for item in credentials if item.get("provider") == provider), 1
        )
    )


def _account_access_summary(
    record: Mapping[str, object], purpose: str, credential_id: str | None = None
) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("账户", identity("accounts", record))
    table.add_row(
        "Provider",
        str(record.get("integration_provider") or record.get("broker") or "—"),
    )
    table.add_row("环境", str(record.get("environment") or "—"))
    segments = record.get("segments")
    table.add_row(
        "产品",
        "/".join(map(str, segments)) if isinstance(segments, list) else "—",
    )
    if purpose:
        table.add_row("Kairos 用途", purpose)
    if credential_id:
        table.add_row("凭据", credential_id)
    permissions = record.get("permissions")
    if isinstance(permissions, Mapping) and permissions:
        table.add_row(
            "Provider 实际权限",
            ", ".join(
                str(name)
                for name, state in permissions.items()
                if str(state).lower() in {"granted", "true", "enabled"}
            )
            or "尚未发现",
        )
    if purpose == "order-trade":
        table.add_row(
            "后果",
            Text(
                "验证通过后可提交、修改和撤销订单；仍不授权资金转移。", style="yellow"
            ),
        )
    elif purpose == "account-read":
        table.add_row("后果", "只读取账户事实，不授权订单命令")
    return table


def _model_provider_actions(
    wizard: ResourceWizardState,
) -> tuple[ActionItem, ...]:
    descriptions = {
        "openai": "使用 Responses API",
        "anthropic": "使用 Claude Messages API",
        "openrouter": "通过统一接口使用多个模型服务",
        "ollama": "连接 Ollama，默认无需 API Key",
        "lmstudio": "连接 LM Studio，默认无需 API Key",
    }
    values = wizard.model_providers or tuple(
        {"provider": provider, "label": label}
        for provider, label in (
            ("openai", "OpenAI"),
            ("anthropic", "Anthropic"),
            ("openrouter", "OpenRouter"),
            ("ollama", "Ollama"),
            ("lmstudio", "LM Studio"),
        )
    )
    actions = tuple(
        ActionItem(
            str(value["provider"]),
            str(value.get("label") or value["provider"])
            + (
                "（推荐）"
                if value["provider"] == "openai"
                else "（本地）"
                if value.get("group") == "local"
                else ""
            ),
            descriptions.get(str(value["provider"]), "连接模型服务"),
            str(index),
        )
        for index, value in enumerate(values, 1)
    )
    return (
        *actions,
        ActionItem(
            "custom",
            "自定义服务",
            "连接兼容接口或企业网关",
            str(len(actions) + 1),
        ),
    )


def _model_endpoint_actions(
    wizard: ResourceWizardState,
) -> tuple[ActionItem, ...]:
    existing = tuple(
        ActionItem(
            str(value["endpoint_id"]),
            str(value["endpoint_id"]),
            str(value.get("provider_label") or value.get("provider") or "模型服务")
            + " · 已配置",
            str(index),
        )
        for index, value in enumerate(wizard.model_endpoints, 1)
    )
    has_existing = bool(existing)
    return (
        *existing,
        ActionItem(
            "__new_endpoint__",
            "添加另一个模型服务" if has_existing else "配置模型服务",
            (
                "选择服务商，并填写 API Key 或本地服务地址"
                if not has_existing
                else "配置完成后返回当前模型"
            ),
            str(len(existing) + 1),
        ),
    )


_MODEL_MODE_ACTIONS = (
    ActionItem("openai-responses", "OpenAI Responses", "兼容 Responses API", "1"),
    ActionItem(
        "openai-chat-completions",
        "OpenAI Chat Completions",
        "兼容 Chat Completions API",
        "2",
    ),
    ActionItem("anthropic-messages", "Anthropic Messages", "兼容 Messages API", "3"),
    ActionItem("ollama-native", "Ollama Native", "使用 Ollama 原生接口", "4"),
)


def _resource_run(
    state: Any,
    session: GuidedSession,
    action: str,
    *,
    value: str | None = None,
) -> RunOperation:
    kind, record = session.resources.kind, session.resources.selected
    assert kind is not None and record is not None

    def operation() -> Any:
        if action in {"test", "discover", "toggle", "delete", "access"} and (
            state.dry_run or state.no_exec
        ):
            return preview_action(kind, record, action, value=value)
        return execute_action(state, kind, record, action, value=value)

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
) -> tuple[ScreenEffect, ...]:
    run = _resource_run(state, session, action, value=value)
    session.resources.action = None
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
    base = context_label(session.context, session.root_label)
    kind = session.resources.kind
    label = RESOURCE_LABELS.get(kind or "")
    if (
        len(session.context) == 2
        and session.context[0] == "resources"
        and session.context[1] in RESOURCE_LABELS
    ):
        return (
            f"{session.root_label} / 运行准备 / {RESOURCE_LABELS[session.context[1]]}"
        )
    if session.context in {
        ("resources", "account-operations"),
        ("resources", "account-order-segments"),
        ("resources", "account-orders"),
    }:
        account = _account_id(session.resources.selected)
        if session.context == ("resources", "account-order-segments"):
            root = f"{session.root_label} / 运行准备 / 订单管理"
            return f"{root} · {account} / 选择交易分区" if account else base
        title = f"{base} · {account}" if account else base
        segment = session.account.selected_segment
        if session.context == ("resources", "account-orders") and segment:
            title = f"{title} / {segment}"
        return title
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
    parent = session.resources.parent_wizard
    session.resources.wizard = None
    session.resources.parent_wizard = None
    if isinstance(parent, ResourceWizardState):
        parent.clear_secrets()
    if isinstance(wizard, ResourceWizardState):
        wizard.clear_secrets()
        session.context = (
            ("resources", "selected")
            if wizard.editing and session.resources.selected
            else ("resources", "ai-models")
            if parent is not None
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


def _chat_activity(
    speaker: str,
    message: str,
    *,
    outcome: ActivityOutcome = ActivityOutcome.SUCCESS,
) -> AppendActivity:
    return AppendActivity(
        ActivityRecord(
            str(uuid4()),
            ActivityKind.QUERY,
            outcome,
            speaker,
            Text(message),
            message,
            f"模型对话 · {speaker}",
        )
    )


def _model_chat_failure_message(result: Mapping[str, object]) -> str:
    summary = str(result.get("detail") or "模型调用失败")
    category = str(result.get("error_category") or "provider_error")
    detail = str(result.get("error_detail") or "模型服务未提供错误详情")
    return f"{summary}\n错误类别：{category}\n错误详情：{detail}"


def _resource_action_outcome(action: str, result: Any) -> ActivityOutcome:
    """Map a completed operation's business result to its visible outcome."""

    if action != "test" or not isinstance(result, Mapping):
        return ActivityOutcome.SUCCESS
    status = str(result.get("verification_status") or "").lower()
    if status == "failed" or result.get("succeeded") is False:
        return ActivityOutcome.FAILURE
    return ActivityOutcome.SUCCESS


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
