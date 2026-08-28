"""Launch configuration, instance, timeline, and attach interaction flow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.panel import Panel
from rich.pretty import Pretty
from rich.text import Text

from kairospy.investment.apps.account.application import AccountConfigurationApplication
from kairospy.strategy.apps.agent.application import AgentResourceApplication
from kairospy.system.apps.integration.application import (
    ProviderConnectionConfigurationApplication,
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
    RefreshLaunchControl,
    RunOperation,
    ScreenEffect,
    SetInteraction,
    SetStatus,
)
from ...catalog import SECTION_ACTIONS
from ...session import GuidedSession
from .actions import (
    ATTACH_ACTIONS,
    INSTANCE_ACTIONS,
    LAUNCH_ACTIONS,
    TIMELINE_ACTIONS,
    LaunchWizardState,
    attach_snapshot as load_launch_attach_snapshot,
    execute as execute_launch,
    export_timeline,
    instance_overview,
    load_components,
    load_instances,
    load_launches,
    load_timeline,
    open_edit_launch_wizard,
    open_new_launch_wizard,
    preview as preview_launch,
    save_launch_wizard,
    send_python,
)
from .views import attach_renderable
from ...navigation import (
    action_id,
    context_items,
    context_label,
    record_description,
    record_label,
)
from ...operation import OperationSpec
from ...results import ResultKind, ResultRoute
from ...selection import (
    LaunchRecordView,
    SelectionRecord,
    selected_value,
    selection_records,
)


def handle_input(
    state: Any, session: GuidedSession, token: ActionToken, value: str
) -> tuple[ScreenEffect, ...] | None:
    """Continue one typed Strategy Launch input."""

    if token.feature is not Feature.STRATEGY:
        return None
    return handle_command(state, session, token.action, (value,))


def attach_operation(state: Any, session: GuidedSession) -> Callable[[], Any] | None:
    """Return the selected Launch attach query for the Textual worker."""

    record = session.strategy.selected_record
    if record is None:
        return None
    return lambda: load_launch_attach_snapshot(state, str(record["launch_id"]))


def handle_command(
    state: Any, session: GuidedSession, command: str, arguments: tuple[str, ...]
) -> tuple[ScreenEffect, ...] | None:
    value = " ".join(arguments)
    if command == "new" and session.context[:1] == ("strategy",):
        return _ask(
            session,
            "strategy:launch-id",
            "请输入新 Launch ID",
            "例如 paper-demo；输入 /back 取消。",
        )
    if command == "strategy:launch-id":
        try:
            wizard = open_new_launch_wizard(state, value.strip())
        except (OSError, ValueError) as error:
            effects = _ask(
                session,
                command,
                "请输入新 Launch ID",
                "例如 paper-demo；输入 /back 取消。",
            )
            return _with_input_error(session, effects, str(error))
        return _start_wizard(state, session, wizard)
    if command.startswith("strategy:launch-field:"):
        wizard = session.strategy.wizard
        if not isinstance(wizard, LaunchWizardState):
            session.enter("strategy")
            return _choice(
                state,
                session,
                Text("Launch 配置向导已经失效。", style="yellow"),
                "向导已失效",
            )
        try:
            wizard.accept(command.removeprefix("strategy:launch-field:"), value)
        except ValueError as error:
            return _input_error(session, str(error))
        return _advance_wizard(state, session, wizard)
    if command == "strategy:launch-save-mode":
        mode = value.strip().lower() or "draft"
        if mode not in {"draft", "publish"}:
            return _input_error(session, "请输入 draft 或 publish")
        return _wizard_confirmation(state, session, publish=mode == "publish")
    if command == "strategy:timeline-export":
        record = session.strategy.selected_record
        if record is None:
            session.enter("strategy")
            return _choice(
                state,
                session,
                Text("实例上下文已经失效。", style="yellow"),
                "实例上下文已失效",
            )
        destination = value.strip()
        launch_id = str(record["launch_id"])
        instance_id = str(record["instance_id"])
        mode = str(record["mode"])

        def operation() -> Any:
            if state.dry_run or state.no_exec:
                return {
                    "status": "preview",
                    "action": "timeline-export",
                    "destination": destination,
                }
            return export_timeline(state, launch_id, instance_id, mode, destination)

        return _confirm_or_run(
            state,
            session,
            _spec(
                "strategy.timeline.export",
                f"导出时间线到 {destination}",
                ResultKind.STRATEGY_TIMELINE_EXPORT,
                operation,
            ),
            dangerous=True,
        )
    if command == "strategy:python":
        record = session.strategy.selected_record
        if record is None:
            session.enter("strategy")
            return _choice(
                state,
                session,
                Text("Launch 上下文已经失效。", style="yellow"),
                "Launch 上下文已失效",
            )
        launch_id = str(record["launch_id"])

        def operation() -> Any:
            if state.dry_run or state.no_exec:
                return {
                    "status": "preview",
                    "action": "interactive.python",
                    "launch_id": launch_id,
                    "source": value,
                }
            return send_python(state, launch_id, value)

        return _confirm_or_run(
            state,
            session,
            _spec(
                "strategy.attach.python",
                f"向 Launch {launch_id} 发送 Strategy Python",
                ResultKind.STRATEGY_ATTACH,
                operation,
            ),
            dangerous=True,
        )
    return None


def handle_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if session.context[:1] != ("strategy",):
        return None
    context = session.context
    record = session.strategy.selected_record
    if context == ("strategy", "setup"):
        wizard = session.strategy.wizard
        if not isinstance(wizard, LaunchWizardState):
            return None
        prompt = wizard.next_prompt()
        if prompt is None:
            return None
        if prompt[0] == "mode":
            selected = action_id(_mode_actions(), command)
            if selected is None:
                return None
            wizard.accept("mode", selected.removeprefix("mode-"))
            return _advance_wizard(state, session, wizard)
        if prompt[0] == "accounts":
            selected = action_id(_account_actions(wizard), command)
            if selected is None:
                return None
            if selected == "accounts-none":
                wizard.selected_accounts.clear()
                wizard.accept("accounts", "")
                return _advance_wizard(state, session, wizard)
            if selected == "accounts-done":
                wizard.accept("accounts", ",".join(sorted(wizard.selected_accounts)))
                return _advance_wizard(state, session, wizard)
            account_id = _selected_account_id(wizard, selected)
            if account_id is None:
                return None
            if account_id in wizard.selected_accounts:
                wizard.selected_accounts.remove(account_id)
            else:
                wizard.selected_accounts.add(account_id)
            return _advance_wizard(state, session, wizard)
        if prompt[0] == "market-profile":
            selected = action_id(_market_connection_actions(wizard), command)
            connection_id = _selected_connection_id(wizard, selected or "")
            if connection_id is None:
                return None
            wizard.accept("market-profile", connection_id)
            return _advance_wizard(state, session, wizard)
        if prompt[0] == "live-trading":
            selected = action_id(_live_access_actions(), command)
            if selected is None:
                return None
            wizard.accept("live-trading", "yes" if selected == "allow-trade" else "no")
            return _advance_wizard(state, session, wizard)
        if prompt[0] != "agent-model-ref":
            return None
        selected = action_id(_model_ref_actions(wizard), command)
        if selected is None:
            return None
        value = ""
        if selected != "unavailable":
            value = _selected_model_ref(wizard, selected) or ""
            if not value:
                return None
        wizard.accept("agent-model-ref", value)
        return _advance_wizard(state, session, wizard)
    if context == ("strategy", "attach"):
        if record is None:
            session.enter("strategy")
            return _choice(state, session)
        action = action_id(ATTACH_ACTIONS, command)
        if action is None:
            return None
        if action == "refresh":
            return RefreshLaunchControl(True), SetStatus("正在刷新运行输出…")
        if action == "pause":
            session.strategy.attach_paused = not session.strategy.attach_paused
            return RefreshLaunchControl(not session.strategy.attach_paused), SetStatus(
                "跟随输出 · 已暂停"
                if session.strategy.attach_paused
                else "跟随输出 · 后台刷新中"
            )
        if action == "clear":
            live_buffer = session.strategy.live_buffer
            if live_buffer is not None:
                live_buffer.clear_visible()
            return RefreshLaunchControl(False), SetStatus(
                "当前日志窗口已清空 · 完整日志未删除"
            )
        return _ask(
            session,
            "strategy:python",
            "请输入一行发送到当前 Strategy 的 Python",
            "执行前会再次确认；输入 /back 取消。",
        )
    if context == ("strategy", "instance"):
        if record is None:
            session.enter("strategy")
            return _choice(state, session)
        action = action_id(INSTANCE_ACTIONS, command)
        if action is None:
            return None
        route = {
            "overview": ResultKind.STRATEGY_INSTANCE,
            "components": ResultKind.STRATEGY_COMPONENTS,
            "timeline": ResultKind.STRATEGY_TIMELINE,
        }[action]
        launch_id = str(record["launch_id"])
        instance_id = str(record["instance_id"])
        mode = str(record["mode"])
        operation = (
            (lambda: instance_overview(state, launch_id, instance_id))
            if action == "overview"
            else (
                (lambda: load_components(state, launch_id, instance_id, mode))
                if action == "components"
                else (lambda: load_timeline(state, launch_id, instance_id, mode))
            )
        )
        return (
            _run(
                f"strategy.instance.{action}",
                f"实例 {instance_id} · {action}",
                route,
                operation,
            ),
        )
    if context == ("strategy", "timeline"):
        if record is None:
            session.enter("strategy")
            return _choice(state, session)
        action = action_id(TIMELINE_ACTIONS, command)
        if action is None:
            return None
        if action == "refresh":
            return (
                _run(
                    "strategy.timeline.refresh",
                    "刷新实例时间线",
                    ResultKind.STRATEGY_TIMELINE,
                    lambda: load_timeline(
                        state,
                        str(record["launch_id"]),
                        str(record["instance_id"]),
                        str(record["mode"]),
                    ),
                ),
            )
        return _ask(
            session,
            "strategy:timeline-export",
            "请输入导出文件路径",
            f"例如 {record['launch_id']}-{record['instance_id']}-timeline.jsonl；输入 /back 取消。",
        )
    if context == ("strategy", "components") and session.visible_records:
        component = _record_choice(session.visible_records, command)
        if not isinstance(component, Mapping):
            return None
        name = str(component.get("component") or "")
        if name in {"market", "execution"}:
            session.context = ("strategy", name)
        body = Panel(
            Pretty(component, expand_all=True),
            title=name.title() if name else "实例组件",
        )
        return _standalone(f"实例组件 · {name or 'detail'}", body), *_choice(
            state, session
        )
    if context == ("strategy", "instances") and session.visible_records:
        instance = _record_choice(session.visible_records, command)
        if not isinstance(instance, Mapping):
            return None
        selected = (record or LaunchRecordView({})).merged(instance)
        session.strategy.selected_record = selected
        session.strategy.instance_entered_from_operations = False
        session.context = ("strategy", "instance")
        body = Panel(Pretty(instance, expand_all=True), title="运行实例")
        return _standalone(f"运行实例 · {instance['instance_id']}", body), *_choice(
            state, session
        )
    if context == ("strategy", "selected"):
        if record is None:
            session.enter("strategy")
            return _choice(state, session)
        action = action_id(LAUNCH_ACTIONS, command)
        if action is None:
            return None
        if action == "edit":
            try:
                wizard = open_edit_launch_wizard(state, record)
            except (OSError, ValueError) as error:
                return _choice(
                    state, session, Text(str(error), style="red"), "无法打开配置"
                )
            return _start_wizard(state, session, wizard)
        if action == "attach":
            session.context = ("strategy", "attach")
            session.strategy.reset_live_buffer(
                f"launch/{record.get('launch_id', 'unknown')}"
            )
            session.strategy.attach_snapshot = None
            return RefreshLaunchControl(True), SetStatus("跟随输出 · 后台刷新中")
        if action == "instances":
            return (
                _run(
                    "strategy.instances",
                    "查看运行实例",
                    ResultKind.STRATEGY_INSTANCES,
                    lambda: load_instances(state, str(record["launch_id"])),
                ),
            )

        def operation() -> Any:
            if action in {"start", "stop", "restart"} and (
                state.dry_run or state.no_exec
            ):
                return preview_launch(record, action)
            return execute_launch(state, record, action)

        spec = _spec(
            f"strategy.launch.{action}",
            f"kairos launch {action} {record['launch_id']}",
            ResultKind.STRATEGY,
            operation,
        )
        return _confirm_or_run(
            state, session, spec, dangerous=action in {"start", "stop", "restart"}
        )
    if context == ("strategy", "launches") and session.visible_records:
        chosen = _record_choice(session.visible_records, command)
        if not isinstance(chosen, Mapping):
            return None
        return enter_selected_record(state, session, chosen)
    action = action_id(SECTION_ACTIONS["strategy"], command)
    if action is None:
        return None
    if action in {"once", "observe", "doctor"}:
        return (
            _run(
                "system.observe",
                "刷新系统状态",
                ResultKind.OBSERVE,
                state.refresh_snapshot,
            ),
        )
    if action == "launch":
        return (
            _run(
                "strategy.launches",
                "查看运行方案",
                ResultKind.STRATEGY_LAUNCHES,
                lambda: load_launches(state),
            ),
        )
    return None


def enter_selected_record(
    state: Any, session: GuidedSession, value: Mapping[str, object]
) -> tuple[ScreenEffect, ...]:
    """Enter the one shared Launch detail from any product entry."""

    selected = LaunchRecordView.from_mapping(value)
    session.strategy.selected_record = selected
    session.context = ("strategy", "selected")
    session.visible_records = ()
    return _choice(
        state,
        session,
        status=f"已选择运行方案 · {record_label(selected)}",
    )


def enter_selected_instance(
    state: Any, session: GuidedSession, value: Mapping[str, object]
) -> tuple[ScreenEffect, ...]:
    """Enter the shared instance detail from the project Operations Center."""

    selected = LaunchRecordView.from_mapping(value)
    session.strategy.selected_record = selected
    session.strategy.instance_records = (selected,)
    session.strategy.instance_entered_from_operations = True
    session.context = ("strategy", "instance")
    session.visible_records = ()
    return _choice(
        state,
        session,
        Panel(Pretty(selected, expand_all=True), title="活动运行实例"),
    )


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    kind = spec.route.kind
    if kind in {
        ResultKind.STRATEGY_LAUNCHES,
        ResultKind.STRATEGY_INSTANCES,
        ResultKind.STRATEGY_COMPONENTS,
    }:
        records = tuple(
            LaunchRecordView.from_mapping(record) for record in (result or ())
        )
        if kind is ResultKind.STRATEGY_LAUNCHES:
            session.strategy.launch_records = records
            context = ("strategy", "launches")
        elif kind is ResultKind.STRATEGY_INSTANCES:
            session.strategy.instance_records = records
            context = ("strategy", "instances")
        else:
            session.strategy.component_records = records
            context = ("strategy", "components")
        session.context = context
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
            title=context_label(context, session.root_label), actions=actions
        )
        session.interaction = interaction
        return SetInteraction(interaction), SetStatus(
            f"找到 {len(records)} 个结果 · 请选择"
        )
    titles = {
        ResultKind.STRATEGY_INSTANCE: "实例概览",
        ResultKind.STRATEGY_TIMELINE_EXPORT: "时间线导出结果",
        ResultKind.STRATEGY_ATTACH: "Launch 运行输出",
        ResultKind.STRATEGY: "Launch 结果",
    }
    if kind is ResultKind.STRATEGY_TIMELINE:
        records = tuple(result or ())
        body = Panel(
            Pretty(records, expand_all=True), title=f"实例时间线 · {len(records)} 条"
        )
        session.context = ("strategy", "timeline")
    elif kind is ResultKind.STRATEGY_WIZARD:
        wizard = session.strategy.wizard
        body = Panel(Pretty(result, expand_all=True), title="Launch 配置结果")
        if isinstance(wizard, LaunchWizardState):
            record = LaunchRecordView(
                {
                    "launch_id": wizard.launch_id,
                    "config": str(
                        result.get("path")
                        if isinstance(result, Mapping) and result.get("path")
                        else wizard.source or ""
                    ),
                    "draft": not (
                        isinstance(result, Mapping)
                        and result.get("status") == "published"
                    ),
                    "mode": wizard.answers.get("mode"),
                }
            )
            session.strategy.selected_record = record
        session.strategy.wizard = None
        session.context = ("strategy", "selected")
    else:
        title = titles.get(kind)
        if title is None:
            return None
        body = Panel(Pretty(result, expand_all=True), title=title)
        if kind is ResultKind.STRATEGY_TIMELINE_EXPORT:
            session.context = ("strategy", "timeline")
        elif kind is ResultKind.STRATEGY_ATTACH:
            session.context = ("strategy", "attach")
    return _activity(spec, body), *_choice(state, session, status="操作已完成")


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind not in _KINDS:
        return None
    if spec.route.kind is ResultKind.STRATEGY_WIZARD:
        cancel_input(session, ActionToken(Feature.STRATEGY, "strategy:launch"))
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
    if spec.route.kind is ResultKind.STRATEGY_WIZARD:
        cancel_input(session, ActionToken(Feature.STRATEGY, "strategy:launch"))
    session.clear_result_flow(spec.route.kind)
    body = Text("操作在开始执行后被取消。", style="yellow")
    return (
        _activity(spec, body, ActivityOutcome.CANCELLED),
        *_choice(state, session, status="操作已取消 · 可继续输入"),
    )


def cancel_input(session: GuidedSession, token: ActionToken) -> bool:
    command = token.action
    if not command.startswith("strategy:launch"):
        return False
    session.strategy.wizard = None
    if session.strategy.selected_record is not None:
        session.context = ("strategy", "selected")
    elif session.strategy.launch_records:
        session.context = ("strategy", "launches")
        session.visible_records = selection_records(
            session.strategy.launch_records,
            label=record_label,
            description=record_description,
        )
    else:
        session.context = ("strategy",)
    return True


def enter_deep_link(
    state: Any,
    session: GuidedSession,
    launch_id: str,
    action: str,
    source: Any | None = None,
) -> tuple[ScreenEffect, ...]:
    fields: dict[str, object] = {"launch_id": launch_id}
    if source is not None:
        fields.update(config=str(source), draft=True)
    record = LaunchRecordView(fields)
    session.strategy.selected_record = record
    session.context = ("strategy", "selected")
    activity = _standalone(
        f"Launch 深链 · {launch_id}",
        Panel(
            f"已进入 Launch {launch_id} 的{'跟随输出' if action == 'attach' else '配置'}流程。",
            title="Launch 深链",
            border_style="cyan",
        ),
    )
    if action == "attach":
        session.context = ("strategy", "attach")
        session.strategy.reset_live_buffer(f"launch/{launch_id}")
        session.strategy.attach_snapshot = None
        return activity, RefreshLaunchControl(True), SetStatus("跟随输出 · 后台刷新中")
    if action == "setup":
        try:
            wizard = LaunchWizardState.open(
                launch_id, Path(str(source)) if source is not None else None
            )
        except (OSError, ValueError) as error:
            return activity, *_choice(
                state, session, Text(str(error), style="red"), "无法打开配置"
            )
        return activity, *_start_wizard(state, session, wizard)
    return activity, *_choice(state, session)


def _start_wizard(
    state: Any, session: GuidedSession, wizard: LaunchWizardState
) -> tuple[ScreenEffect, ...]:
    session.strategy.wizard = wizard
    session.context = ("strategy", "setup")
    return _advance_wizard(state, session, wizard)


def _advance_wizard(
    state: Any, session: GuidedSession, wizard: LaunchWizardState
) -> tuple[ScreenEffect, ...]:
    prompt = wizard.next_prompt()
    if prompt:
        name, label, detail = prompt
        if name == "agent-model-ref":
            wizard.model_refs = (
                AgentResourceApplication(state.owner).verified_model_refs()
                if state.owner is not None
                else ()
            )
            interaction = ChoiceInteraction(
                title=f"{context_label(session.context, session.root_label)} · 选择 Agent 模型",
                summary=Text(
                    "仅显示当前配置下已完成最小文本调用验证的模型。"
                    if wizard.model_refs
                    else "当前没有已验证模型；可以继续保存未完成草稿，但不能发布 Launch。",
                    style="dim" if wizard.model_refs else "yellow",
                ),
                actions=_model_ref_actions(wizard),
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus(
                "请选择 Agent 模型"
                if wizard.model_refs
                else "没有可用模型 · 可保存未完成草稿"
            )
        if name == "mode":
            interaction = ChoiceInteraction(
                title=f"{context_label(session.context, session.root_label)} · 运行模式",
                summary=Text("选择 Launch 的运行边界。", style="dim"),
                actions=_mode_actions(),
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("请选择运行模式")
        if name == "accounts":
            if not wizard.account_records and state.owner is not None:
                try:
                    wizard.account_records = tuple(
                        AccountConfigurationApplication(state.owner).list()
                    )
                except (OSError, RuntimeError, ValueError):
                    wizard.account_records = ()
                wizard.selected_accounts.update(
                    item.strip()
                    for item in wizard._default("accounts").split(",")
                    if item.strip()
                )
            interaction = ChoiceInteraction(
                title=f"{context_label(session.context, session.root_label)} · 选择账户",
                summary=Text(
                    "选择一个或多个 Account；这里不会选择或显示 API Key。"
                    "完成后再决定只读观察或允许交易。",
                    style="dim",
                ),
                actions=_account_actions(wizard),
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("请选择账户后完成")
        if name == "market-profile":
            if not wizard.provider_connections and state.owner is not None:
                try:
                    wizard.provider_connections = tuple(
                        item
                        for item in ProviderConnectionConfigurationApplication(
                            state.owner
                        ).list()
                        if item.get("enabled")
                        and "market-query" in _strings(item.get("purposes"))
                    )
                except (OSError, ValueError):
                    wizard.provider_connections = ()
            interaction = ChoiceInteraction(
                title=f"{context_label(session.context, session.root_label)} · 选择行情连接",
                summary=Text(
                    "仅显示启用且声明 market-query 的 Provider Connection。",
                    style="dim",
                ),
                actions=_market_connection_actions(wizard),
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("请选择行情连接")
        if name == "live-trading":
            interaction = ChoiceInteraction(
                title=f"{context_label(session.context, session.root_label)} · 账户使用方式",
                summary=Text(
                    "只读观察只要求 account-read；允许交易还会要求 order-trade binding、"
                    "Provider 实测交易权限和后续安全约束。",
                    style="yellow",
                ),
                actions=_live_access_actions(),
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("请选择账户使用方式")
        return _ask(session, f"strategy:launch-field:{name}", label, detail)
    return _ask(
        session,
        "strategy:launch-save-mode",
        "保存方式（draft / publish）",
        "draft 仅保存草稿；publish 校验并发布。",
        Pretty(wizard.preview(), expand_all=True),
    )


def _model_ref_actions(wizard: LaunchWizardState) -> tuple[ActionItem, ...]:
    if not wizard.model_refs:
        return (
            ActionItem(
                "unavailable",
                "暂不选择模型",
                "继续完成向导并保存为未完成草稿",
                "1",
            ),
        )
    return tuple(
        ActionItem(
            f"model-ref-{index}",
            str(value["model_ref"]),
            f"{value.get('provider_label') or value.get('provider') or '模型服务'} · 已验证",
            str(index),
        )
        for index, value in enumerate(wizard.model_refs, 1)
    )


def _mode_actions() -> tuple[ActionItem, ...]:
    return (
        ActionItem("mode-backtest", "回测", "历史事件回放，不连接真实账户", "1"),
        ActionItem("mode-paper", "模拟运行", "实时行情 + 模拟账户", "2"),
        ActionItem("mode-live", "实时运行", "真实账户；交易能力需要额外授权", "3"),
    )


def _account_actions(wizard: LaunchWizardState) -> tuple[ActionItem, ...]:
    actions = [
        ActionItem("accounts-done", "完成选择", "使用当前勾选的 Account", "1"),
        ActionItem("accounts-none", "不使用账户", "仅运行不依赖账户的策略", "2"),
    ]
    actions.extend(
        ActionItem(
            f"account-{index}",
            f"{'✓ ' if str(record.get('account_id')) in wizard.selected_accounts else ''}"
            f"{record.get('account_id')}",
            f"{record.get('integration_provider') or record.get('broker') or 'unknown'} · "
            f"{record.get('environment') or record.get('mode') or 'unknown'}",
            str(index + 2),
        )
        for index, record in enumerate(wizard.account_records, 1)
    )
    return tuple(actions)


def _selected_account_id(wizard: LaunchWizardState, action: str) -> str | None:
    if not action.startswith("account-"):
        return None
    try:
        record = wizard.account_records[int(action.removeprefix("account-")) - 1]
    except (ValueError, IndexError):
        return None
    return str(record.get("account_id") or "") or None


def _market_connection_actions(wizard: LaunchWizardState) -> tuple[ActionItem, ...]:
    if not wizard.provider_connections:
        return (
            ActionItem(
                "connection-unavailable",
                "没有可用行情连接",
                "请先在资源配置中添加并测试 Provider Connection",
                "1",
            ),
        )
    return tuple(
        ActionItem(
            f"connection-{index}",
            str(record.get("connection_id")),
            f"{record.get('provider')} · "
            f"{', '.join(_strings(record.get('products')))} · "
            f"{record.get('verification_status') or 'pending'}",
            str(index),
        )
        for index, record in enumerate(wizard.provider_connections, 1)
    )


def _selected_connection_id(wizard: LaunchWizardState, action: str) -> str | None:
    if not action.startswith("connection-") or action == "connection-unavailable":
        return None
    try:
        record = wizard.provider_connections[
            int(action.removeprefix("connection-")) - 1
        ]
    except (ValueError, IndexError):
        return None
    return str(record.get("connection_id") or "") or None


def _live_access_actions() -> tuple[ActionItem, ...]:
    return (
        ActionItem(
            "observe-only",
            "只读观察",
            "读取余额、持仓和订单状态，不允许创建真实订单",
            "1",
        ),
        ActionItem(
            "allow-trade",
            "允许交易",
            "要求同一 Account 显式配置 order-trade 并通过权限验证",
            "2",
        ),
    )


def _selected_model_ref(wizard: LaunchWizardState, action: str) -> str | None:
    if not action.startswith("model-ref-"):
        return None
    try:
        index = int(action.removeprefix("model-ref-")) - 1
        value = wizard.model_refs[index]
    except (ValueError, IndexError):
        return None
    return str(value.get("model_ref") or "") or None


def _wizard_confirmation(
    state: Any, session: GuidedSession, *, publish: bool
) -> tuple[ScreenEffect, ...]:
    wizard = session.strategy.wizard
    if not isinstance(wizard, LaunchWizardState):
        return _choice(
            state,
            session,
            Text("Launch 配置向导已经失效。", style="yellow"),
            "向导已失效",
        )

    def operation() -> Any:
        if state.dry_run or state.no_exec:
            return {
                "status": "preview",
                "action": "publish" if publish else "save-draft",
                "launch_id": wizard.launch_id,
                "summary": wizard.preview(),
            }
        return save_launch_wizard(state, wizard, publish=publish)

    return _confirm_or_run(
        state,
        session,
        _spec(
            "strategy.wizard.save",
            f"{'发布' if publish else '保存草稿'} Launch {wizard.launch_id}",
            ResultKind.STRATEGY_WIZARD,
            operation,
        ),
        dangerous=True,
        details=Pretty(wizard.preview(), expand_all=True),
        title="Launch 配置确认",
    )


def _ask(
    session: GuidedSession,
    action: str,
    prompt: str,
    detail: str,
    summary: Any | None = None,
) -> tuple[ScreenEffect, ...]:
    session.ask(
        ActionToken(Feature.STRATEGY, action),
        title=context_label(session.context, session.root_label),
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


def _with_input_error(
    session: GuidedSession, effects: tuple[ScreenEffect, ...], error: str
) -> tuple[ScreenEffect, ...]:
    _input_error(session, error)
    return SetInteraction(session.interaction), effects[-1]


def _confirm_or_run(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
    *,
    dangerous: bool,
    details: Any | None = None,
    title: str = "需要确认",
) -> tuple[ScreenEffect, ...]:
    if not dangerous or state.yes or state.dry_run or state.no_exec:
        return (RunOperation(spec),)
    session.confirm(spec, title=title, display_summary=details)
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
        title=context_label(session.context, session.root_label),
        summary=summary,
        actions=context_items(session, state),
    )
    session.interaction = interaction
    return SetInteraction(interaction), SetStatus(status)


def _record_choice(records: tuple[SelectionRecord, ...], value: str) -> object | None:
    return selected_value(records, value)


def _strings(value: object) -> tuple[str, ...]:
    """Validate and normalize a list retained in a presentation record."""

    return tuple(str(item) for item in value) if isinstance(value, list) else ()


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
        ResultKind.STRATEGY_LAUNCHES,
        ResultKind.STRATEGY_INSTANCES,
        ResultKind.STRATEGY_COMPONENTS,
        ResultKind.STRATEGY_INSTANCE,
        ResultKind.STRATEGY_TIMELINE,
        ResultKind.STRATEGY_TIMELINE_EXPORT,
        ResultKind.STRATEGY_ATTACH,
        ResultKind.STRATEGY,
        ResultKind.STRATEGY_WIZARD,
    }
)
__all__ = [
    "cancel_input",
    "enter_deep_link",
    "handle_cancel",
    "handle_command",
    "handle_context",
    "handle_failure",
    "handle_success",
]
