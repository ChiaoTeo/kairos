"""Strategy, Execution, and launch-scoped Market interaction flow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.panel import Panel
from rich.pretty import Pretty
from rich.text import Text

from ...widgets import (
    ActionToken,
    ActionItem,
    ChoiceInteraction,
    Feature,
    InputInteraction,
    renderable_plain_text,
)
from ..activity import ActivityKind, ActivityOutcome, ActivityRecord
from ..effects import (
    AppendActivity,
    RefreshLaunchControl,
    RunOperation,
    ScreenEffect,
    SetInteraction,
    SetStatus,
)
from ..guided.catalog import SECTION_ACTIONS
from ..guided.execution import (
    EXECUTION_ACTIONS,
    ExecutionPromptState,
    execute as execute_execution,
    preview as preview_execution,
)
from ..guided.launch_market import (
    MARKET_COMPONENT_ACTIONS,
    LaunchMarketPromptState,
    execute as execute_launch_market,
    preview as preview_launch_market,
)
from ..guided.models import GuidedSession
from ..guided.strategy import (
    ATTACH_ACTIONS,
    INSTANCE_ACTIONS,
    LAUNCH_ACTIONS,
    TIMELINE_ACTIONS,
    LaunchWizardState,
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
from ..navigation import (
    action_id,
    context_items,
    context_label,
    record_description,
    record_label,
)
from ..operation import OperationSpec
from ..results import ResultKind, ResultRoute


def handle_input(
    state: Any, session: GuidedSession, token: ActionToken, value: str
) -> tuple[ScreenEffect, ...] | None:
    """Continue one typed Strategy, Execution, or Launch Market input."""

    if token.feature is not Feature.STRATEGY:
        return None
    return handle_command(state, session, token.action, (value,))


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
        return _start_wizard(session, wizard)
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
        return _advance_wizard(session, wizard)
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
    if command.startswith("execution:field:"):
        prompt = session.strategy.execution_prompt
        if not isinstance(prompt, ExecutionPromptState):
            session.context = ("strategy", "execution")
            return _choice(
                state,
                session,
                Text("Execution 参数向导已经失效。", style="yellow"),
                "向导已失效",
            )
        try:
            prompt.accept(command.removeprefix("execution:field:"), value)
        except ValueError as error:
            return _input_error(session, str(error))
        return _advance_execution(state, session, prompt)
    if command.startswith("launch-market:field:"):
        prompt = session.strategy.launch_market_prompt
        if not isinstance(prompt, LaunchMarketPromptState):
            session.context = ("strategy", "market")
            return _choice(
                state,
                session,
                Text("Market 参数向导已经失效。", style="yellow"),
                "向导已失效",
            )
        try:
            prompt.accept(command.removeprefix("launch-market:field:"), value)
        except ValueError as error:
            return _input_error(session, str(error))
        return _advance_market(state, session, prompt)
    return None


def handle_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if session.context[:1] != ("strategy",):
        return None
    context = session.context
    record = session.strategy.selected_record
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
    if context == ("strategy", "execution"):
        if record is None:
            session.enter("strategy")
            return _choice(state, session)
        action = action_id(EXECUTION_ACTIONS, command)
        if action is None:
            return None
        prompt = ExecutionPromptState(action, record)
        session.strategy.execution_prompt = prompt
        return _advance_execution(state, session, prompt)
    if context == ("strategy", "market"):
        if record is None:
            session.enter("strategy")
            return _choice(state, session)
        action = action_id(MARKET_COMPONENT_ACTIONS, command)
        if action is None:
            return None
        selected = state.selected_market
        default = (
            str(selected.id) if selected is not None and hasattr(selected, "id") else ""
        )
        prompt = LaunchMarketPromptState(action, record, default)
        session.strategy.launch_market_prompt = prompt
        return _advance_market(state, session, prompt)
    if context == ("strategy", "components") and session.visible_records:
        component = _record_choice(session.visible_records, command)
        if component is None:
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
        if instance is None:
            return None
        selected = dict(record or {})
        selected.update(dict(instance))
        session.strategy.selected_record = selected
        session.context = ("strategy", "instance")
        state.selected_launch_instance = str(instance["instance_id"])
        state.selected_launch_mode = str(instance["mode"])
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
            return _start_wizard(session, wizard)
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
        if chosen is None:
            return None
        selected = dict(chosen)
        session.strategy.selected_record = selected
        session.context = ("strategy", "selected")
        state.selected_launch = str(selected["launch_id"])
        return _standalone(
            f"Launch · {selected['launch_id']}",
            Panel(Pretty(selected, expand_all=True), title="Launch"),
        ), *_choice(state, session)
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
                "查看 Launch 列表",
                ResultKind.STRATEGY_LAUNCHES,
                lambda: load_launches(state),
            ),
        )
    return None


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    kind = spec.route.kind
    if kind in {
        ResultKind.STRATEGY_LAUNCHES,
        ResultKind.STRATEGY_INSTANCES,
        ResultKind.STRATEGY_COMPONENTS,
    }:
        records = tuple(dict(record) for record in (result or ()))
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
        session.visible_records = records
        actions = tuple(
            ActionItem(str(i), record_label(record), record_description(record), str(i))
            for i, record in enumerate(records, 1)
        )
        interaction = ChoiceInteraction(title=context_label(context), actions=actions)
        session.interaction = interaction
        return SetInteraction(interaction), SetStatus(
            f"找到 {len(records)} 个结果 · 请选择"
        )
    titles = {
        ResultKind.STRATEGY_INSTANCE: "实例概览",
        ResultKind.STRATEGY_TIMELINE_EXPORT: "时间线导出结果",
        ResultKind.STRATEGY_ATTACH: "Launch 运行输出",
        ResultKind.STRATEGY: "Launch 结果",
        ResultKind.EXECUTION: "Execution 结果",
        ResultKind.LAUNCH_MARKET: "Market 组件结果",
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
            record = {
                "launch_id": wizard.launch_id,
                "config": str(
                    result.get("path")
                    if isinstance(result, Mapping) and result.get("path")
                    else wizard.source or ""
                ),
                "draft": not (
                    isinstance(result, Mapping) and result.get("status") == "published"
                ),
                "mode": wizard.answers.get("mode"),
            }
            session.strategy.selected_record = record
            state.selected_launch = wizard.launch_id
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
        elif kind is ResultKind.EXECUTION:
            session.strategy.execution_prompt = None
        elif kind is ResultKind.LAUNCH_MARKET:
            session.strategy.launch_market_prompt = None
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
        session.visible_records = session.strategy.launch_records
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
    record: dict[str, Any] = {"launch_id": launch_id}
    if source is not None:
        record.update(config=str(source), draft=True)
    state.selected_launch = launch_id
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
        return activity, *_start_wizard(session, wizard)
    return activity, *_choice(state, session)


def _advance_execution(
    state: Any, session: GuidedSession, prompt: ExecutionPromptState
) -> tuple[ScreenEffect, ...]:
    next_prompt = prompt.next_prompt()
    if next_prompt:
        name, label, detail = next_prompt
        return _ask(
            session,
            f"execution:field:{name}",
            label,
            detail,
            Pretty(prompt.summary(), expand_all=True),
        )

    def operation() -> Any:
        return (
            preview_execution(prompt)
            if state.dry_run or state.no_exec
            else execute_execution(state, prompt)
        )

    return _confirm_or_run(
        state,
        session,
        _spec(
            f"strategy.execution.{prompt.action}",
            f"Execution {prompt.action} {prompt.launch_id}/{prompt.instance_id}",
            ResultKind.EXECUTION,
            operation,
        ),
        dangerous=prompt.dangerous,
        details=Pretty(prompt.summary(), expand_all=True),
        title="Execution 作用域确认",
    )


def _advance_market(
    state: Any, session: GuidedSession, prompt: LaunchMarketPromptState
) -> tuple[ScreenEffect, ...]:
    next_prompt = prompt.next_prompt()
    if next_prompt:
        name, label, detail = next_prompt
        return _ask(
            session,
            f"launch-market:field:{name}",
            label,
            detail,
            Pretty(prompt.summary(), expand_all=True),
        )

    def operation() -> Any:
        return (
            preview_launch_market(prompt)
            if state.dry_run or state.no_exec
            else execute_launch_market(state, prompt)
        )

    return _confirm_or_run(
        state,
        session,
        _spec(
            f"strategy.market.{prompt.action}",
            f"Market {prompt.action} {prompt.launch.get('launch_id')}/{prompt.launch.get('instance_id')}",
            ResultKind.LAUNCH_MARKET,
            operation,
        ),
        dangerous=prompt.dangerous,
        details=Pretty(prompt.summary(), expand_all=True),
        title="Market 组件操作确认",
    )


def _start_wizard(
    session: GuidedSession, wizard: LaunchWizardState
) -> tuple[ScreenEffect, ...]:
    session.strategy.wizard = wizard
    session.context = ("strategy", "setup")
    return _advance_wizard(session, wizard)


def _advance_wizard(
    session: GuidedSession, wizard: LaunchWizardState
) -> tuple[ScreenEffect, ...]:
    prompt = wizard.next_prompt()
    if prompt:
        name, label, detail = prompt
        return _ask(session, f"strategy:launch-field:{name}", label, detail)
    return _ask(
        session,
        "strategy:launch-save-mode",
        "保存方式（draft / publish）",
        "draft 仅保存草稿；publish 校验并发布。",
        Pretty(wizard.preview(), expand_all=True),
    )


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
        title=context_label(session.context),
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
        title=context_label(session.context),
        summary=summary,
        actions=context_items(session, state),
    )
    session.interaction = interaction
    return SetInteraction(interaction), SetStatus(status)


def _record_choice(records: tuple[Any, ...], value: str) -> Any | None:
    try:
        index = int(value) - 1
    except ValueError:
        return None
    return records[index] if 0 <= index < len(records) else None


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
        ResultKind.EXECUTION,
        ResultKind.LAUNCH_MARKET,
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
