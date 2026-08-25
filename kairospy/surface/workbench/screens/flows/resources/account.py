"""Account runtime and order interaction within Resources."""

from __future__ import annotations

from collections.abc import Mapping
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
from .account_actions import ACCOUNT_ACTIONS, execute as execute_account
from ...session import GuidedSession
from ..launch.orders import (
    ORDER_ACTIONS,
    OrderPromptState,
    execute as execute_order,
    preview as preview_order,
)
from .actions import identity
from ...navigation import action_id, context_items, context_label
from ...operation import OperationSpec
from ...results import ResultKind, ResultRoute


def handle_input(
    state: Any, session: GuidedSession, token: ActionToken, value: str
) -> tuple[ScreenEffect, ...] | None:
    if token.feature is not Feature.RESOURCES or not token.action.startswith(
        ("account:", "order:")
    ):
        return None
    return handle_command(state, session, token.action, (value,))


def cancel_input(session: GuidedSession, token: ActionToken) -> bool:
    if token.feature is not Feature.RESOURCES or not token.action.startswith(
        ("account:", "order:")
    ):
        return False
    session.account.order_prompt = None
    return True


def handle_command(
    state: Any, session: GuidedSession, command: str, arguments: tuple[str, ...]
) -> tuple[ScreenEffect, ...] | None:
    value = " ".join(arguments).strip()
    if command == "account:fees":
        record = session.resources.selected
        if record is None:
            session.enter("resources")
            return _choice(
                state,
                session,
                Text("账户上下文已经失效。", style="yellow"),
                "账户上下文已失效",
            )
        return (
            _run(
                "account.fees",
                f"{identity('accounts', record)} · 费率",
                ResultKind.ACCOUNT,
                lambda: execute_account(state, record, "fees", value),
            ),
        )
    if not command.startswith("order:field:"):
        return None
    prompt = session.account.order_prompt
    if not isinstance(prompt, OrderPromptState):
        session.context = ("resources", "account-orders")
        return _choice(
            state,
            session,
            Text("订单参数向导已经失效。", style="yellow"),
            "向导已失效",
        )
    try:
        prompt.accept(command.removeprefix("order:field:"), value)
    except ValueError as error:
        return _input_error(session, str(error))
    return _advance_order(state, session, prompt)


def handle_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if session.context == ("resources", "account-operations"):
        record = session.resources.selected
        if record is None:
            session.enter("resources")
            return _choice(state, session)
        action = action_id(ACCOUNT_ACTIONS, command)
        if action is None:
            return None
        if action == "fees":
            return _ask(
                session,
                "account:fees",
                "费率范围（产品:交易对）",
                "直接回车使用 spot:BTCUSDT；输入 /back 取消。",
            )
        if action == "orders":
            session.context = ("resources", "account-orders")
            return _choice(state, session)
        return (
            _run(
                f"account.{action}",
                f"{identity('accounts', record)} · {action}",
                ResultKind.ACCOUNT,
                lambda: execute_account(state, record, action),
            ),
        )
    if session.context != ("resources", "account-orders"):
        return None
    record = session.resources.selected
    if record is None:
        session.enter("resources")
        return _choice(state, session)
    action = action_id(ORDER_ACTIONS, command)
    if action is None:
        return None
    prompt = OrderPromptState(action, dict(record))
    session.account.order_prompt = prompt
    return _advance_order(state, session, prompt)


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    kind = spec.route.kind
    if kind not in {ResultKind.ACCOUNT, ResultKind.ORDER}:
        return None
    account = _account_id(session.resources.selected)
    label = "订单操作结果" if kind is ResultKind.ORDER else "账户运行结果"
    body = Panel(
        Pretty(result, expand_all=True),
        title=f"{account} · {label}" if account else label,
    )
    if kind is ResultKind.ORDER:
        session.account.order_prompt = None
    return _activity(spec, body), *_choice(state, session, status="操作已完成")


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    if spec.route.kind not in _KINDS:
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
    if spec.route.kind not in _KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
    body = Text("操作在开始执行后被取消。", style="yellow")
    return (
        _activity(spec, body, ActivityOutcome.CANCELLED),
        *_choice(state, session, status="操作已取消 · 可继续输入"),
    )


def _advance_order(
    state: Any, session: GuidedSession, prompt: OrderPromptState
) -> tuple[ScreenEffect, ...]:
    next_prompt = prompt.next_prompt()
    if next_prompt:
        name, label, detail = next_prompt
        return _ask(
            session,
            f"order:field:{name}",
            label,
            detail,
            Pretty(prompt.summary(), expand_all=True),
        )

    def operation() -> Any:
        return (
            preview_order(prompt)
            if state.dry_run or state.no_exec
            else execute_order(state, prompt)
        )

    spec = _spec(
        f"account.order.{prompt.action}",
        f"{prompt.action} account={prompt.account_id}",
        ResultKind.ORDER,
        operation,
    )
    if prompt.dangerous and not (state.yes or state.dry_run or state.no_exec):
        session.confirm(
            spec,
            title="订单作用域确认",
            display_summary=Pretty(prompt.summary(), expand_all=True),
        )
        return SetInteraction(session.interaction), SetStatus("等待确认")
    return (RunOperation(spec),)


def _ask(
    session: GuidedSession,
    action: str,
    prompt: str,
    detail: str,
    summary: Any | None = None,
) -> tuple[ScreenEffect, ...]:
    session.ask(
        ActionToken(Feature.RESOURCES, action),
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
    return SetInteraction(session.interaction), SetStatus("输入有误 · 请修正")


def _run(
    action: str,
    summary: str,
    kind: ResultKind,
    operation: Callable[[], Any],
) -> RunOperation:
    return RunOperation(_spec(action, summary, kind, operation))


def _spec(
    action: str,
    summary: str,
    kind: ResultKind,
    operation: Callable[[], Any],
) -> OperationSpec:
    return OperationSpec.create(
        action_name=action,
        audit_summary=summary,
        route=ResultRoute(kind),
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


def _account_id(record: Mapping[str, Any] | None) -> str:
    return str(record.get("account_id") or "").strip() if record else ""


_KINDS = frozenset({ResultKind.ACCOUNT, ResultKind.ORDER})


__all__ = [
    "cancel_input",
    "handle_cancel",
    "handle_command",
    "handle_context",
    "handle_failure",
    "handle_input",
    "handle_success",
]
