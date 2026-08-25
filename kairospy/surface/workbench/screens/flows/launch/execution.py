"""Launch-scoped Execution interaction flow."""

from __future__ import annotations

from typing import Any, Callable

from rich.panel import Panel
from rich.pretty import Pretty
from rich.text import Text

from ....widgets import (
    ActionToken,
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
from .execution_actions import (
    EXECUTION_ACTIONS,
    ExecutionPromptState,
    execute as execute_execution,
    preview as preview_execution,
)
from ...session import GuidedSession
from ...navigation import action_id, context_items, context_label
from ...operation import OperationSpec
from ...results import ResultKind, ResultRoute


def handle_input(
    state: Any, session: GuidedSession, token: ActionToken, value: str
) -> tuple[ScreenEffect, ...] | None:
    if token.feature is not Feature.STRATEGY or not token.action.startswith(
        "execution:"
    ):
        return None
    return handle_command(state, session, token.action, (value,))


def cancel_input(session: GuidedSession, token: ActionToken) -> bool:
    if token.feature is not Feature.STRATEGY or not token.action.startswith(
        "execution:"
    ):
        return False
    session.execution.reset()
    return True


def handle_command(
    state: Any, session: GuidedSession, command: str, arguments: tuple[str, ...]
) -> tuple[ScreenEffect, ...] | None:
    if not command.startswith("execution:field:"):
        return None
    prompt = session.execution.prompt
    if not isinstance(prompt, ExecutionPromptState):
        session.context = ("strategy", "execution")
        return _choice(
            state,
            session,
            Text("Execution 参数向导已经失效。", style="yellow"),
            "向导已失效",
        )
    try:
        prompt.accept(command.removeprefix("execution:field:"), " ".join(arguments))
    except ValueError as error:
        return _input_error(session, str(error))
    return _advance(state, session, prompt)


def handle_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if session.context != ("strategy", "execution"):
        return None
    record = session.strategy.selected_record
    if record is None:
        session.enter("strategy")
        return _choice(state, session)
    action = action_id(EXECUTION_ACTIONS, command)
    if action is None:
        return None
    prompt = ExecutionPromptState(action, record)
    session.execution.prompt = prompt
    return _advance(state, session, prompt)


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind is not ResultKind.EXECUTION:
        return None
    session.execution.reset()
    body = Panel(Pretty(result, expand_all=True), title="Execution 结果")
    return _activity(spec, body), *_choice(state, session, status="操作已完成")


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind is not ResultKind.EXECUTION:
        return None
    session.execution.reset()
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
    if spec.route.kind is not ResultKind.EXECUTION:
        return None
    session.execution.reset()
    body = Text("操作在开始执行后被取消。", style="yellow")
    return (
        _activity(spec, body, ActivityOutcome.CANCELLED),
        *_choice(state, session, status="操作已取消 · 可继续输入"),
    )


def _advance(
    state: Any, session: GuidedSession, prompt: ExecutionPromptState
) -> tuple[ScreenEffect, ...]:
    next_prompt = prompt.next_prompt()
    if next_prompt:
        name, label, detail = next_prompt
        session.ask(
            ActionToken(Feature.STRATEGY, f"execution:field:{name}"),
            title=context_label(session.context),
            prompt=label,
            detail=detail,
            value_summary=Pretty(prompt.summary(), expand_all=True),
        )
        return SetInteraction(session.interaction), SetStatus("等待输入")

    def operation() -> Any:
        return (
            preview_execution(prompt)
            if state.dry_run or state.no_exec
            else execute_execution(state, prompt)
        )

    spec = _spec(
        f"execution.{prompt.action}",
        f"Execution {prompt.action}",
        operation,
    )
    if prompt.dangerous and not (state.yes or state.dry_run or state.no_exec):
        session.confirm(spec, display_summary=Pretty(prompt.summary(), expand_all=True))
        return SetInteraction(session.interaction), SetStatus("等待确认")
    return (RunOperation(spec),)


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
    return SetInteraction(session.interaction), SetStatus("输入有误 · 请修正")


def _spec(action: str, summary: str, operation: Callable[[], Any]) -> OperationSpec:
    return OperationSpec.create(
        action_name=action,
        audit_summary=summary,
        route=ResultRoute(ResultKind.EXECUTION),
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
        title=context_label(session.context),
        summary=summary,
        actions=context_items(session, state),
    )
    session.interaction = interaction
    return SetInteraction(interaction), SetStatus(status)


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


__all__ = [
    "cancel_input",
    "handle_cancel",
    "handle_command",
    "handle_context",
    "handle_failure",
    "handle_input",
    "handle_success",
]
