"""User-facing account and trading runtime flow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from kairospy.investment.apps.account.application import AccountConfigurationApplication

from ....widgets import (
    ActionItem,
    ActionToken,
    ChoiceInteraction,
    Feature,
    InputInteraction,
    renderable_plain_text,
)
from ...activity import ActivityKind, ActivityOutcome, ActivityRecord
from ...presentation import ResultTone, conclusion, facts
from ...effects import (
    AppendActivity,
    RunOperation,
    ScreenEffect,
    SetInteraction,
    SetStatus,
)
from .account_actions import ACCOUNT_ACTIONS, execute as execute_account
from .account_transfers import (
    TRANSFER_RESULT_ACTIONS,
    TransferPromptState,
    confirm as confirm_transfer,
    history as transfer_history,
    preview as preview_transfer,
    status as transfer_status,
    transfer_available,
    unavailable_result as transfer_unavailable_result,
)
from ...session import GuidedSession
from ..launch.orders import (
    ORDER_ACTIONS,
    OrderPromptState,
    execute as execute_order,
    order_segment_actions,
    order_segments,
    preview as preview_order,
)
from .actions import identity
from .views import record_summary
from ...navigation import action_id, context_items, context_label
from ...operation import OperationSpec
from ...results import ResultKind, ResultRoute
from ...selection import ResourceRecordView, selection_records


def enter_accounts(state: Any, session: GuidedSession) -> tuple[ScreenEffect, ...]:
    """Open Account runtime tasks without entering connection management."""

    session.account.runtime_entry = True
    session.resources.kind = "accounts"
    session.resources.selected = None
    session.account.selected = None
    session.context = ("account", "accounts")
    return (
        _run(
            "account.list",
            "查看交易账户",
            ResultKind.RESOURCE_LIST,
            lambda: AccountConfigurationApplication(_owner(state)).list(),
            qualifier="account-runtime",
        ),
    )


def handle_input(
    state: Any, session: GuidedSession, token: ActionToken, value: str
) -> tuple[ScreenEffect, ...] | None:
    if token.feature is not Feature.RESOURCES or not token.action.startswith(
        ("account:", "order:", "transfer:")
    ):
        return None
    return handle_command(state, session, token.action, (value,))


def cancel_input(session: GuidedSession, token: ActionToken) -> bool:
    if token.feature is not Feature.RESOURCES or not token.action.startswith(
        ("account:", "order:", "transfer:")
    ):
        return False
    session.account.order_prompt = None
    session.account.transfer_prompt = None
    return True


def handle_command(
    state: Any, session: GuidedSession, command: str, arguments: tuple[str, ...]
) -> tuple[ScreenEffect, ...] | None:
    value = " ".join(arguments).strip()
    if command == "account:fees":
        record = session.account.selected
        if record is None:
            session.enter("account", "accounts")
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
    if command.startswith("transfer:field:"):
        prompt = session.account.transfer_prompt
        if not isinstance(prompt, TransferPromptState):
            session.context = ("account", "selected")
            return _choice(
                state,
                session,
                Text("资金划转向导已经失效。", style="yellow"),
                "向导已失效",
            )
        try:
            prompt.accept(command.removeprefix("transfer:field:"), value)
        except ValueError as error:
            return _input_error(session, str(error))
        return _advance_transfer(state, session, prompt)
    if not command.startswith("order:field:"):
        return None
    prompt = session.account.order_prompt
    if not isinstance(prompt, OrderPromptState):
        session.context = ("account", "orders")
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
    if session.context == ("account", "accounts"):
        if not session.visible_records:
            if command not in {"configure", "1"}:
                return None
            session.account.runtime_entry = False
            session.enter("resources")
            return _choice(state, session, status="请配置交易账户")
        record = _record_choice(session.visible_records, command)
        if not isinstance(record, Mapping):
            return None
        session.account.selected = ResourceRecordView.from_mapping(record)
        session.context = ("account", "selected")
        return _choice(state, session, status="已进入账户与交易")
    if session.context == ("account", "selected"):
        record = session.account.selected
        if record is None:
            session.enter("account", "accounts")
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
            segments = order_segments(record)
            session.account.reset()
            session.account.selected = record
            if len(segments) == 1:
                session.account.selected_segment = segments[0]
                session.context = ("account", "orders")
            else:
                session.context = ("account", "order-segments")
            if not segments:
                return _choice(
                    state,
                    session,
                    Text("当前账户没有配置交易分区。", style="yellow"),
                    "无法进入订单管理 · 请先配置交易分区",
                )
            return _choice(state, session)
        if action == "transfer":
            if not transfer_available(record):
                return (
                    _run(
                        "account.transfer.unavailable",
                        f"{identity('accounts', record)} · 资金划转能力",
                        ResultKind.TRANSFER,
                        lambda: transfer_unavailable_result(record),
                    ),
                )
            prompt = TransferPromptState(dict(record))
            session.account.transfer_prompt = prompt
            return _advance_transfer(state, session, prompt)
        if action == "connection":
            session.account.runtime_entry = False
            session.resources.kind = "accounts"
            session.resources.selected = record
            session.context = ("resources", "selected")
            return _choice(
                state,
                session,
                Text("管理当前账户的连接、访问权限与配置。", style="dim"),
            )
        return (
            _run(
                f"account.{action}",
                f"{identity('accounts', record)} · {action}",
                ResultKind.ACCOUNT,
                lambda: execute_account(state, record, action),
            ),
        )
    if session.context == ("account", "order-segments"):
        record = session.account.selected
        if record is None:
            session.enter("account", "accounts")
            return _choice(state, session)
        segment = action_id(order_segment_actions(record), command)
        if segment is None:
            return None
        session.account.selected_segment = segment
        session.account.order_prompt = None
        session.context = ("account", "orders")
        return _choice(
            state,
            session,
            Text(f"当前交易分区：{segment}", style="dim"),
        )
    if session.context == ("account", "transfer-result"):
        prompt = session.account.transfer_prompt
        if not isinstance(prompt, TransferPromptState):
            session.context = ("account", "selected")
            return _choice(state, session)
        action = action_id(TRANSFER_RESULT_ACTIONS, command)
        if action is None:
            return None
        if action == "again":
            replacement = TransferPromptState(dict(prompt.account))
            session.account.transfer_prompt = replacement
            session.context = ("account", "selected")
            return _advance_transfer(state, session, replacement)
        if action == "history":
            return (
                _run(
                    "account.transfer.history",
                    f"{prompt.source_account_id} · 划转历史",
                    ResultKind.TRANSFER,
                    lambda: transfer_history(state, prompt),
                ),
            )
        plan_id = _transfer_plan_id(prompt)
        if not plan_id:
            return _input_error(session, "当前没有可查询的划转计划")
        return (
            _run(
                "account.transfer.status",
                f"{prompt.source_account_id} · 划转状态 {plan_id}",
                ResultKind.TRANSFER,
                lambda: transfer_status(state, prompt, plan_id),
            ),
        )
    if session.context != ("account", "orders"):
        return None
    record = session.account.selected
    if record is None:
        session.enter("account", "accounts")
        return _choice(state, session)
    action = action_id(ORDER_ACTIONS, command)
    if action is None:
        return None
    values: dict[str, str] = {}
    if session.account.selected_segment:
        values["segment"] = session.account.selected_segment
    prompt = OrderPromptState(action, dict(record), values)
    session.account.order_prompt = prompt
    return _advance_order(state, session, prompt)


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    kind = spec.route.kind
    if kind is ResultKind.RESOURCE_LIST and spec.route.qualifier == "account-runtime":
        records = tuple(
            ResourceRecordView.from_mapping(record) for record in (result or ())
        )
        session.resources.kind = "accounts"
        session.resources.selected = None
        session.account.records = records
        session.account.selected = None
        session.context = ("account", "accounts")
        visible = selection_records(
            records,
            key=lambda record: identity("accounts", record),
            label=lambda record: identity("accounts", record),
            description=lambda record: record_summary("accounts", record),
        )
        session.visible_records = visible
        actions = tuple(
            ActionItem(str(index), record.label, record.description, str(index))
            for index, record in enumerate(visible, 1)
        )
        if not actions:
            actions = (
                ActionItem(
                    "configure",
                    "配置交易账户",
                    "前往连接与配置添加账户",
                    "1",
                ),
            )
        interaction = ChoiceInteraction(title=context_title(session), actions=actions)
        session.interaction = interaction
        return SetInteraction(interaction), SetStatus(
            f"找到 {len(records)} 个账户 · 请选择"
            if records
            else "尚未配置交易账户"
        )
    if kind not in {ResultKind.ACCOUNT, ResultKind.ORDER, ResultKind.TRANSFER}:
        return None
    if spec.action_name == "account.transfer.preview" and isinstance(result, Mapping):
        prompt = session.account.transfer_prompt
        preview = result.get("preview")
        if isinstance(prompt, TransferPromptState) and isinstance(preview, Mapping):
            prompt.preview = dict(preview)
            body = _transfer_result_renderable(result, title="资金划转预览")
            confirm_spec = _spec(
                "account.transfer.confirm",
                f"{prompt.source_account_id} · 确认资金划转",
                ResultKind.TRANSFER,
                lambda: confirm_transfer(state, prompt),
            )
            session.confirm(
                confirm_spec,
                title="实盘资金划转确认",
                display_summary=body,
                force_hint="确认后会向交易所提交真实资金划转。",
            )
            return (
                _activity(spec, body),
                SetInteraction(session.interaction),
                SetStatus("等待明确确认"),
            )
    account = _account_id(session.account.selected)
    label = (
        "订单操作结果"
        if kind is ResultKind.ORDER
        else "资金划转结果"
        if kind is ResultKind.TRANSFER
        else "账户运行结果"
    )
    title = f"{account} · {label}" if account else label
    body = (
        _transfer_result_renderable(result, title=title)
        if kind is ResultKind.TRANSFER and isinstance(result, Mapping)
        else _account_result_renderable(spec.action_name, result, title=title)
    )
    if kind is ResultKind.ORDER:
        session.account.order_prompt = None
    if (
        kind is ResultKind.TRANSFER
        and spec.action_name != "account.transfer.unavailable"
    ):
        session.context = ("account", "transfer-result")
        unknown = isinstance(result, Mapping) and bool(result.get("result_unknown"))
        status = "划转结果未知 · 请查询本次状态" if unknown else "资金划转操作已完成"
        return _activity(spec, body), *_choice(state, session, status=status)
    return _activity(spec, body), *_choice(state, session, status="操作已完成")


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    if (
        spec.route.kind is ResultKind.RESOURCE_LIST
        and spec.route.qualifier != "account-runtime"
    ):
        return None
    if spec.route.kind not in _KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
    message = Text(error, style="red")
    return (
        _activity(spec, message, ActivityOutcome.FAILURE),
        *_choice(
            state,
            session,
            None,
            "操作失败 · 可重试、返回或查看帮助",
        ),
    )


def handle_cancel(
    state: Any, session: GuidedSession, spec: OperationSpec
) -> tuple[ScreenEffect, ...] | None:
    if (
        spec.route.kind is ResultKind.RESOURCE_LIST
        and spec.route.qualifier != "account-runtime"
    ):
        return None
    if spec.route.kind not in _KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
    body = Text("操作在开始执行后被取消。", style="yellow")
    return (
        _activity(spec, body, ActivityOutcome.CANCELLED),
        *_choice(state, session, status="操作已取消 · 可继续输入"),
    )


def _advance_transfer(
    state: Any, session: GuidedSession, prompt: TransferPromptState
) -> tuple[ScreenEffect, ...]:
    next_prompt = prompt.next_prompt()
    if next_prompt:
        name, label, detail = next_prompt
        return _ask(
            session,
            f"transfer:field:{name}",
            label,
            detail,
            Pretty(prompt.summary(), expand_all=True),
        )
    if state.dry_run or state.no_exec:
        return (
            _run(
                "account.transfer.preview",
                f"{prompt.source_account_id} · 资金划转预览",
                ResultKind.TRANSFER,
                lambda: {"status": "dry-run-preview", **prompt.summary()},
            ),
        )
    return (
        _run(
            "account.transfer.preview",
            f"{prompt.source_account_id} · 资金划转预览",
            ResultKind.TRANSFER,
            lambda: preview_transfer(state, prompt),
        ),
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
        title=context_title(session),
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
) -> OperationSpec:
    return OperationSpec.create(
        action_name=action,
        audit_summary=summary,
        route=ResultRoute(kind, qualifier),
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
        title=context_title(session),
        summary=summary,
        actions=context_items(session, state),
    )
    session.interaction = interaction
    return SetInteraction(interaction), SetStatus(status)


def context_title(session: GuidedSession) -> str:
    """Return the Account-owned task and selected-object breadcrumb."""
    base = context_label(session.context, session.root_label)
    account = _account_id(session.account.selected)
    if session.context == ("account", "accounts"):
        return f"{session.root_label} / 账户与交易"
    if session.context == ("account", "order-segments"):
        root = f"{session.root_label} / 账户与交易 / 订单管理"
        return f"{root} · {account} / 选择交易分区" if account else base
    if session.context in {
        ("account", "selected"),
        ("account", "orders"),
    }:
        title = f"{base} · {account}" if account else base
        if (
            session.context == ("account", "orders")
            and session.account.selected_segment
        ):
            title = f"{title} / {session.account.selected_segment}"
        return title
    return base


def _record_choice(records: tuple[Any, ...], command: str) -> Any | None:
    if not command.isdecimal():
        return None
    index = int(command) - 1
    if index < 0 or index >= len(records):
        return None
    return records[index].value


def _owner(state: Any) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的项目")
    return state.owner


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


def _account_id(record: Mapping[str, Any] | None) -> str:
    return str(record.get("account_id") or "").strip() if record else ""


def _account_result_renderable(
    action: str, result: Any, *, title: str
) -> RenderableType:
    """Render Account-owned query results as business facts, not JSON records."""

    if action == "account.overview" and isinstance(result, Mapping):
        return _overview_renderable(result, title=title)
    if action == "account.assets" and isinstance(result, Mapping):
        return _assets_renderable(result, title=title)
    if action == "account.positions" and isinstance(result, Mapping):
        return _positions_renderable(result, title=title)
    if action == "account.earn" and isinstance(result, Mapping):
        return _earn_renderable(result, title=title)
    if action == "account.fees" and isinstance(result, Mapping):
        return _fees_renderable(result, title=title)
    if isinstance(result, Mapping):
        rows = tuple(
            (label, str(result[key]))
            for key, label in (
                ("status", "状态"),
                ("account_id", "Account ID"),
                ("segment_key", "Segment"),
                ("detail", "说明"),
                ("reason", "原因"),
            )
            if key in result and result[key] is not None
        )
        preview = str(result.get("status") or "").lower() == "preview"
        return Group(
            conclusion(
                f"{title}预演完成，未执行任何修改" if preview else f"{title}已完成",
                tone=ResultTone.PREVIEW if preview else ResultTone.SUCCESS,
            ),
            facts(rows) if rows else Text("没有更多业务字段", style="dim"),
        )
    return conclusion(str(result) or f"{title}已完成")


def _transfer_result_renderable(
    result: Mapping[str, Any], *, title: str
) -> RenderableType:
    if result.get("available") is False:
        unavailable = Group(
            Text("资金划转不可用", style="bold yellow"),
            Text(str(result.get("message") or "当前账户不具备资金划转权限。")),
            Text("请查看账户权限或更换交易账户。", style="dim"),
        )
        return Panel(unavailable, title=title, border_style="yellow")
    transfers = _mapping_rows(result.get("transfers"))
    if "transfers" in result:
        table = Table(show_header=True, header_style="bold")
        for column in ("计划", "资产", "金额", "方向", "计划状态", "参与方状态"):
            table.add_column(column)
        for item in transfers:
            plan = _mapping(item.get("plan"))
            operation = _mapping(item.get("operation"))
            source = _mapping(plan.get("source"))
            destination = _mapping(plan.get("destination"))
            table.add_row(
                _value(plan.get("plan_id")),
                _value(source.get("asset")),
                _value(plan.get("amount")),
                f"{_location_text(source)} → {_location_text(destination)}",
                _localized(plan.get("status")),
                _localized(operation.get("status")),
            )
        body: RenderableType = (
            table if transfers else Text("尚无资金划转记录。", style="dim")
        )
        return Panel(body, title=title, border_style="cyan")
    preview = _mapping(result.get("preview"))
    plan = _mapping(result.get("plan"))
    operation = _mapping(result.get("operation"))
    facts = preview or plan
    source = _mapping(facts.get("source"))
    destination = _mapping(facts.get("destination"))
    table = Table(show_header=True, header_style="bold")
    table.add_column("划转字段")
    table.add_column("值")
    rows = [
        ("环境", "一次性 Provider 直连"),
        ("转出", _location_text(source)),
        ("转入", _location_text(destination)),
        ("资产", source.get("asset") or destination.get("asset")),
        ("金额", facts.get("amount")),
    ]
    if preview:
        rows.extend(
            (
                ("当前转出可用", preview.get("source_observed_available")),
                ("划转后转出可用", result.get("source_available_after")),
                ("当前转入可用", preview.get("destination_observed_available")),
                ("划转后转入可用", result.get("destination_available_after")),
                ("预览到期(NS)", preview.get("expires_at")),
                ("预览状态", "等待明确确认"),
            )
        )
    else:
        rows.extend(
            (
                ("计划 ID", plan.get("plan_id")),
                ("计划状态", _localized(plan.get("status"))),
                ("参与方状态", _localized(operation.get("status"))),
                ("交易所流水", operation.get("participant_operation_id")),
                ("提交次数", operation.get("attempt_count")),
            )
        )
    for label, value in rows:
        table.add_row(label, _value(value))
    content: list[RenderableType] = [table]
    if result.get("result_unknown"):
        content.extend(
            (
                Text(),
                Text(
                    "结果未知：请使用“查询本次状态”，不要重新提交。",
                    style="bold yellow",
                ),
            )
        )
    failure = operation.get("failure_reason")
    if failure:
        content.extend((Text(), Text(str(failure), style="yellow")))
    return Panel(Group(*content), title=title, border_style="cyan")


def _location_text(value: Mapping[str, Any]) -> str:
    account = _value(value.get("account_id"))
    segment = _value(value.get("segment"))
    return f"{account} / {segment}"


def _transfer_plan_id(prompt: TransferPromptState) -> str:
    if not isinstance(prompt.preview, Mapping):
        return ""
    return str(prompt.preview.get("plan_id") or "")


def _overview_renderable(result: Mapping[str, Any], *, title: str) -> RenderableType:
    identity = _mapping(result.get("identity"))
    connection = _mapping(result.get("connection"))
    profile = _mapping(result.get("profile"))
    permissions = _mapping(result.get("permissions"))
    commercial = _mapping(result.get("commercial"))
    facts = _mapping(result.get("facts"))
    health = _mapping(result.get("health"))

    account = str(identity.get("account_id") or "—")
    heading = Text(
        f"账户 {account} · {_localized(health.get('mode'))} · {_localized(health.get('source'))}",
        style="bold",
    )
    overview = Table(show_header=True, header_style="bold")
    overview.add_column("账户概览")
    overview.add_column("值")
    rows = (
        ("账户", identity.get("account_id")),
        ("别名", identity.get("alias")),
        ("券商/托管方", identity.get("broker")),
        ("交易所", identity.get("exchange")),
        ("环境", _localized(identity.get("environment"))),
        ("集成提供方", connection.get("integration_adapter")),
        (
            "配置账户模式",
            _model(profile.get("configured_account_model"), "未配置（采用实测）"),
        ),
        ("实测账户模式", _model(profile.get("observed_account_model"), "未能确认")),
        (
            "Provider 原生模式",
            _model(profile.get("provider_account_model"), "未能确认"),
        ),
        ("模式一致性", _localized(profile.get("model_match"))),
        ("统一保证金账户", _optional_bool(profile.get("unified"))),
        ("保证金模式", _localized(profile.get("margin_mode"), "不适用或未返回")),
        ("持仓模式", _localized(profile.get("position_mode"), "未能查询")),
        ("费率", _localized(commercial.get("fee_summary_status"))),
        ("VIP 等级", commercial.get("vip_tier") or "未返回"),
        ("有效权限", _joined(permissions.get("effective_capabilities"))),
        ("非零资产", facts.get("non_zero_balance_count")),
        ("保证金资产", facts.get("collateral_count")),
        ("交易持仓", facts.get("position_count")),
        ("理财持有", _optional_count(facts.get("earn_holding_count"))),
        ("未完成订单", _optional_count(facts.get("open_order_count"))),
        ("数据完整度", _localized(health.get("completeness"))),
        ("健康状态", _localized(health.get("overall_status"))),
        ("数据时效", _localized(health.get("freshness"))),
        (
            "账户分区",
            f"{_value(health.get('segments_succeeded'))}/{_value(health.get('segments_requested'))}",
        ),
    )
    for label, value in rows:
        overview.add_row(label, _value(value))

    content: list[RenderableType] = [heading, Text(), overview]
    segments = _mapping_rows(profile.get("segments"))
    if segments:
        segment_table = Table(show_header=True, header_style="bold")
        for column in (
            "分区",
            "完整度",
            "时效",
            "实测模式",
            "原生模式",
            "观测时间(NS)",
            "问题",
        ):
            segment_table.add_column(column)
        for segment in segments:
            segment_table.add_row(
                _value(segment.get("segment")),
                _localized(segment.get("completeness")),
                _localized(segment.get("freshness")),
                _model(segment.get("observed_account_model"), "不适用"),
                _model(segment.get("provider_account_model"), "未返回"),
                _value(segment.get("observed_at_unix_nanos")),
                _value(segment.get("issue")),
            )
        content.extend((Text(), segment_table))

    issues = _mapping_rows(health.get("issues"))
    if issues:
        content.extend((Text(), Text("查询问题", style="bold yellow")))
        content.extend(
            Text(
                f"- {issue.get('segment') or '未知分区'}：{issue.get('message') or '查询失败'}",
                style="yellow",
            )
            for issue in issues
        )
    return Panel(Group(*content), title=title, border_style="cyan")


def _assets_renderable(result: Mapping[str, Any], *, title: str) -> RenderableType:
    table = Table(show_header=True, header_style="bold")
    for column in ("分区", "类别", "资产", "总额", "可用", "锁定", "借入", "利息"):
        table.add_column(column)

    balances = _mapping_rows(result.get("balances"))
    collateral = _mapping_rows(result.get("collateral"))
    balance_keys = {_balance_key(row) for row in balances}
    collateral_keys = {_balance_key(row) for row in collateral}
    rows = (
        *balances,
        *(row for row in collateral if _balance_key(row) not in balance_keys),
    )
    for row in rows:
        key = _balance_key(row)
        role = str(row.get("role") or "—")
        if key in balance_keys and key in collateral_keys:
            role = "钱包/保证金"
        else:
            role = {"wallet": "钱包", "collateral": "保证金"}.get(role, role)
        table.add_row(
            str(row.get("segment") or "—"),
            role,
            str(row.get("asset") or "—"),
            _value(row.get("total")),
            _value(row.get("available")),
            _value(row.get("locked")),
            _value(row.get("borrowed")),
            _value(row.get("interest")),
        )

    account = str(result.get("account_id") or "—")
    completeness = {
        "complete": "完整",
        "partial": "部分完整",
        "unavailable": "不可用",
    }.get(
        str(result.get("completeness") or "").lower(),
        str(result.get("completeness") or "—"),
    )
    mode = {"standalone": "独立查询", "connected": "运行实例"}.get(
        str(result.get("mode") or ""), str(result.get("mode") or "—")
    )
    source = {
        "direct_provider": "接入直连",
        "local_registry": "本地配置",
    }.get(str(result.get("source") or ""), str(result.get("source") or "—"))
    succeeded = _value(result.get("segments_succeeded"))
    requested = _value(result.get("segments_requested"))
    heading = Text(
        f"账户 {account} · {mode} · {source} · 完整度 {completeness} · 分区 {succeeded}/{requested}",
        style="bold",
    )
    content: list[RenderableType] = [heading]
    if balances or collateral:
        content.extend((Text(), table))
    else:
        content.extend((Text(), Text("没有返回资产余额。", style="dim")))

    issues = _query_issues(result)
    if issues:
        content.extend((Text(), Text("查询问题", style="bold yellow")))
        content.extend(Text(f"- {issue}", style="yellow") for issue in issues)
    return Panel(Group(*content), title=title, border_style="cyan")


def _positions_renderable(result: Mapping[str, Any], *, title: str) -> RenderableType:
    positions = _mapping_rows(result.get("positions"))
    content: list[RenderableType] = [_query_heading(result, include_segments=True)]
    if positions:
        table = Table(show_header=True, header_style="bold")
        for column in (
            "分区",
            "交易标的",
            "方向",
            "数量",
            "开仓均价",
            "标记价格",
            "未实现盈亏",
            "已实现盈亏",
            "保证金模式",
            "持仓模式",
        ):
            table.add_column(column)
        for position in positions:
            table.add_row(
                _value(position.get("segment")),
                _value(position.get("symbol")),
                _localized(position.get("side")),
                _value(position.get("quantity")),
                _value(position.get("average_price")),
                _value(position.get("mark_price")),
                _value(position.get("unrealized_pnl")),
                _value(position.get("realized_pnl")),
                _localized(position.get("margin_mode")),
                _localized(position.get("position_mode")),
            )
        content.extend((Text(), table))
    else:
        message = (
            "当前没有交易仓位。现货和资金账户资产请在“资产与余额”中查看。"
            if _is_complete(result)
            else "未能确定所请求范围内的交易仓位。"
        )
        content.extend((Text(), Text(message, style="dim")))
    _append_query_issues(content, result)
    return Panel(Group(*content), title=title, border_style="cyan")


def _earn_renderable(result: Mapping[str, Any], *, title: str) -> RenderableType:
    holdings = _mapping_rows(result.get("holdings"))
    content: list[RenderableType] = [_query_heading(result)]
    if holdings:
        table = Table(show_header=True, header_style="bold")
        for column in (
            "分区",
            "类型",
            "产品",
            "资产",
            "本金",
            "可赎回",
            "流动性",
            "到期时间(NS)",
            "状态",
            "累计奖励",
        ):
            table.add_column(column)
        for holding in holdings:
            table.add_row(
                _value(holding.get("segment")),
                _value(holding.get("family")),
                _value(holding.get("product_id")),
                _value(holding.get("asset")),
                _value(holding.get("principal")),
                _value(holding.get("redeemable_amount")),
                _localized(holding.get("liquidity")),
                _value(holding.get("matures_at_unix_nanos")),
                _localized(holding.get("state")),
                _rewards(holding.get("accrued_rewards")),
            )
        content.extend((Text(), table))
    else:
        message = (
            "当前没有理财或质押持有。"
            if _is_complete(result)
            else "未能确定所请求范围内的理财或质押持有。"
        )
        content.extend((Text(), Text(message, style="dim")))
    _append_query_issues(content, result)
    return Panel(Group(*content), title=title, border_style="cyan")


def _fees_renderable(result: Mapping[str, Any], *, title: str) -> RenderableType:
    content: list[RenderableType] = [_query_heading(result)]
    summary = Table(show_header=True, header_style="bold")
    summary.add_column("费率字段")
    summary.add_column("值")
    vip_tier = result.get("vip_tier") or _localized(result.get("vip_tier_status"))
    for label, value in (
        ("产品", result.get("product")),
        ("交易对", result.get("symbol")),
        ("VIP 等级", vip_tier),
        ("RPI 费率", result.get("rpi")),
        ("观测时间(NS)", result.get("observed_at_unix_nanos")),
    ):
        summary.add_row(label, _value(value))
    content.extend((Text(), summary))

    rates = Table(show_header=True, header_style="bold")
    for column in ("费率类别", "Maker", "Taker", "买方", "卖方"):
        rates.add_column(column)
    components = (
        ("实际费率", result),
        ("标准费率", _mapping(result.get("standard"))),
        ("特殊费率", _mapping(result.get("special"))),
        ("税费", _mapping(result.get("tax"))),
    )
    for label, component in components:
        if label != "实际费率" and not component:
            continue
        rates.add_row(
            label,
            _value(component.get("maker")),
            _value(component.get("taker")),
            _value(component.get("buyer")),
            _value(component.get("seller")),
        )
    content.extend((Text(), rates))

    discount = _mapping(result.get("discount"))
    if discount:
        discount_table = Table(show_header=True, header_style="bold")
        discount_table.add_column("折扣字段")
        discount_table.add_column("值")
        for label, value in (
            ("账户已启用", _optional_bool(discount.get("enabled_for_account"))),
            ("交易对已启用", _optional_bool(discount.get("enabled_for_symbol"))),
            ("折扣资产", discount.get("asset")),
            ("折扣率", discount.get("rate")),
        ):
            discount_table.add_row(label, _value(value))
        content.extend((Text(), discount_table))

    issues = result.get("issues")
    if isinstance(issues, list) and issues:
        content.extend((Text(), Text("说明", style="bold yellow")))
        content.extend(Text(f"- {issue}", style="yellow") for issue in issues)
    return Panel(Group(*content), title=title, border_style="cyan")


def _mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(row for row in value if isinstance(row, Mapping))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _balance_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(row.get(field))
        for field in (
            "segment",
            "asset",
            "total",
            "available",
            "locked",
            "borrowed",
            "interest",
        )
    )


def _query_issues(result: Mapping[str, Any]) -> tuple[str, ...]:
    issues: list[str] = []
    for error in _mapping_rows(result.get("errors")):
        issues.append(
            f"{error.get('segment') or '未知分区'}：{error.get('message') or '查询失败'}"
        )
    for outcome in _mapping_rows(result.get("outcomes")):
        status = str(outcome.get("outcome") or "").lower()
        message = outcome.get("message")
        if status not in {"", "complete"} or message:
            issues.append(
                f"{outcome.get('segment') or '未知分区'}：{message or status or '查询不完整'}"
            )
    return tuple(issues)


def _query_heading(
    result: Mapping[str, Any], *, include_segments: bool = False
) -> Text:
    parts = [
        f"账户 {_value(result.get('account_id'))}",
        _localized(result.get("mode")),
        _localized(result.get("source")),
        f"完整度 {_localized(result.get('completeness'))}",
    ]
    if include_segments:
        parts.append(
            f"分区 {_value(result.get('segments_succeeded'))}/{_value(result.get('segments_requested'))}"
        )
    return Text(" · ".join(parts), style="bold")


def _append_query_issues(
    content: list[RenderableType], result: Mapping[str, Any]
) -> None:
    issues = _query_issues(result)
    if not issues:
        return
    content.extend((Text(), Text("查询问题", style="bold yellow")))
    content.extend(Text(f"- {issue}", style="yellow") for issue in issues)


def _is_complete(result: Mapping[str, Any]) -> bool:
    return str(result.get("completeness") or "").lower() == "complete"


def _rewards(value: Any) -> str:
    rewards = _mapping_rows(value)
    return (
        "、".join(
            f"{_value(reward.get('asset'))} {_value(reward.get('amount'))}"
            for reward in rewards
        )
        or "—"
    )


def _value(value: Any) -> str:
    return "—" if value is None else str(value)


def _localized(value: Any, missing: str = "—") -> str:
    if value is None or value == "":
        return missing
    text = str(value)
    return {
        "live": "实盘",
        "paper": "模拟",
        "standalone": "独立查询",
        "connected": "运行实例",
        "direct_provider": "接入直连",
        "local_registry": "本地配置",
        "complete": "完整",
        "partial": "部分完整",
        "unavailable": "不可用",
        "unsupported": "不支持",
        "unauthorized": "未授权",
        "not_applicable": "不适用",
        "not_queried": "尚未查询",
        "ready": "正常",
        "configured": "已配置",
        "fresh": "新鲜",
        "local": "本地",
        "read": "只读",
        "trade": "交易",
        "match": "一致",
        "mismatch": "不一致",
        "not_configured": "未配置，无法比较",
        "not_observed": "尚未观测",
        "cross": "全仓",
        "isolated": "逐仓",
        "one_way": "单向持仓",
        "hedge": "双向持仓",
        "buy": "多",
        "long": "多",
        "sell": "空",
        "short": "空",
        "flexible": "活期",
        "locked": "定期",
        "active": "持有中",
        "redeemable": "可赎回",
        "matured": "已到期",
        "query_by_symbol": "按交易对查询",
        "included_in_observed_rate": "已包含在实测费率中",
        "authorized": "已授权",
        "transferring": "提交中",
        "awaiting_transfer": "等待交易所结果",
        "reconciling": "核对中",
        "completed": "已完成",
        "indeterminate": "结果未知",
        "rejected": "已拒绝",
        "expired": "已过期",
        "failed": "失败",
        "prepared": "已准备",
        "dispatching": "提交中（结果未知）",
        "awaiting_participant": "等待交易所结果",
        "awaiting_account_observation": "等待余额确认",
        "settled": "已结算",
    }.get(text.lower(), text)


def _model(value: Any, missing: str) -> str:
    if value is None or value == "":
        return missing
    text = str(value)
    return {
        "portfolio_margin_pro": "统一账户 Pro（Portfolio Margin Pro）",
        "portfolio_margin": "统一账户（Portfolio Margin）",
        "contract_unified": "统一合约账户",
        "unified": "统一账户",
        "contract": "合约账户",
        "margin": "保证金账户",
        "cross_margin": "保证金账户",
        "no_margin": "现货账户",
        "spot": "现货账户",
        "classic_futures": "经典合约账户",
        "funding_wallet": "资金钱包",
        "multiple": "多个分区模式（见下表）",
    }.get(text.lower(), text)


def _optional_bool(value: Any) -> str:
    return "是" if value is True else "否" if value is False else "未能确认"


def _optional_count(value: Any) -> str:
    return "未能查询" if value is None else str(value)


def _joined(value: Any) -> str:
    if not isinstance(value, list):
        return "—"
    return "、".join(_localized(item) for item in value) or "—"


_KINDS = frozenset(
    {ResultKind.ACCOUNT, ResultKind.ORDER, ResultKind.TRANSFER, ResultKind.RESOURCE_LIST}
)


__all__ = [
    "cancel_input",
    "enter_accounts",
    "handle_cancel",
    "handle_command",
    "handle_context",
    "handle_failure",
    "handle_input",
    "handle_success",
]
