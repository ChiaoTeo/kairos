"""Market interaction flow owned by its Workbench product slice."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from time import time_ns
from typing import Any
from uuid import uuid4

from rich.panel import Panel
from rich.pretty import Pretty
from rich.text import Text

from ....widgets import (
    ActionToken,
    ActionItem,
    ChoiceInteraction,
    ConfirmInteraction,
    ControlInteraction,
    Feature,
    InputInteraction,
    renderable_plain_text,
)
from ...activity import ActivityKind, ActivityOutcome, ActivityRecord
from ...effects import (
    AppendActivity,
    RefreshMarketControl,
    RunOperation,
    ScreenEffect,
    SetInteraction,
    SetStatus,
)
from ...catalog import MARKET_ADVANCED_ACTIONS, SECTION_ACTIONS
from .actions import (
    MARKET_CONTROL_ACTIONS,
    MarketFilePromptState,
    MarketRouteView,
    diagnostic_command,
    execute_file_action,
    file_command,
    file_result_renderable,
    load_datasets,
    load_observation,
    load_routes,
    observation_command,
    observation_renderable,
    preview_file_action,
    route_diagnostic_renderable,
    route_command,
    run_diagnostic,
    selected_market_actions,
)
from ...session import GuidedSession
from ..reference.actions import load_records
from .workspace import (
    WORKSPACE_MARKET_ACTIONS,
    WorkspaceMarketPromptState,
    equivalent_command as workspace_market_command,
    execute as execute_workspace_market,
    preview as preview_workspace_market,
)
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
    """Continue one typed Market input interaction."""

    if token.feature is Feature.MARKET:
        if token.action == "search":
            return handle_command(state, session, "market", (value,))
        if token.action == "file-field" and token.field is not None:
            return handle_command(
                state, session, f"market-file:field:{token.field}", (value,)
            )
        if token.action == "workspace-field" and token.field is not None:
            return handle_command(
                state, session, f"workspace-market:field:{token.field}", (value,)
            )
    return None


def cancel_input(session: GuidedSession, token: ActionToken) -> bool:
    """Clear only the Market prompt owned by the token."""

    if token.feature is not Feature.MARKET:
        return False
    if token.action == "file-field":
        session.market.file_prompt = None
    elif token.action == "workspace-field":
        session.market.workspace_prompt = None
    return True


def live_observation_operation(
    state: Any, session: GuidedSession
) -> Callable[[], Any] | None:
    """Return the current Market refresh operation for the Textual worker."""

    market = session.market.selected
    observation = session.market.observation
    provider = session.market.provider
    if market is None or observation is None or provider is None:
        return None
    return lambda: load_observation(state, market, observation, provider)


def selected_title(session: GuidedSession) -> str | None:
    market = session.market.selected
    if market is None:
        return None
    values = [record_label(market)]
    exchange_id = getattr(market, "exchange_id", None)
    if exchange_id:
        values.append(str(exchange_id).rsplit(":", 1)[-1])
    instrument_kind = getattr(market, "instrument_kind", None)
    if instrument_kind:
        values.append(str(instrument_kind))
    return " · ".join(values)


def handle_command(
    state: Any,
    session: GuidedSession,
    command: str,
    arguments: tuple[str, ...],
) -> tuple[ScreenEffect, ...] | None:
    """Handle typed input commands owned by Market."""

    if command == "market":
        query = " ".join(arguments).strip()
        if not query:
            return _ask_market(session)
        return (_run_market_search(state, session, query),)

    if command.startswith("market-file:field:"):
        prompt = session.market.file_prompt
        if not isinstance(prompt, MarketFilePromptState):
            session.enter("market")
            return _choice(
                state,
                session,
                summary=Text(
                    "Market 文件操作参数向导已经失效，请重新选择操作。",
                    style="yellow",
                ),
                status="参数向导已失效",
            )
        return _accept_prompt_field(
            state,
            session,
            prompt,
            command.removeprefix("market-file:field:"),
            " ".join(arguments),
            workspace=False,
        )

    if command.startswith("workspace-market:field:"):
        prompt = session.market.workspace_prompt
        if not isinstance(prompt, WorkspaceMarketPromptState):
            session.context = ("market", "connected")
            return _choice(
                state,
                session,
                summary=Text("Workspace Market 参数向导已经失效。", style="yellow"),
                status="参数向导已失效",
            )
        return _accept_prompt_field(
            state,
            session,
            prompt,
            command.removeprefix("workspace-market:field:"),
            " ".join(arguments),
            workspace=True,
        )
    return None


def handle_context(
    state: Any,
    session: GuidedSession,
    command: str,
) -> tuple[ScreenEffect, ...] | None:
    """Advance a Market navigation context."""

    if session.context[:1] == ("market",):
        return _handle_market_context(state, session, command)
    return None


def handle_success(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
    result: Any,
) -> tuple[ScreenEffect, ...] | None:
    """Interpret result kinds owned by the Market product slice."""

    kind = spec.route.kind
    if kind is ResultKind.MARKET:
        records = tuple(result or ())
        visible = _visible(records)
        session.market.records = visible
        if records:
            return _show_record_choices(session, "market", visible)
        session.enter("market")
        interaction = ChoiceInteraction(
            title="首页 / 市场行情",
            summary=Text("没有找到匹配的市场标的。", style="dim"),
            actions=SECTION_ACTIONS["market"],
        )
        return SetInteraction(interaction), SetStatus("没有找到匹配结果")

    if kind is ResultKind.MARKET_OBSERVATION:
        market = session.market.selected
        if market is None:
            session.enter("market")
            interaction = ChoiceInteraction(
                title="首页 / 市场行情",
                summary=Text("行情上下文已经失效，请重新选择标的。", style="yellow"),
                actions=SECTION_ACTIONS["market"],
            )
            return SetInteraction(interaction), SetStatus("行情上下文已失效")

        body = observation_renderable(result)
        session.market.snapshot = body
        session.context = ("market", "selected")
        session.visible_records = session.market.records
        actions = context_items(session, state)
        interaction = (
            ControlInteraction(
                title=record_label(market),
                snapshot=body,
                actions=actions,
                refreshing=True,
            )
            if session.market.refresh_enabled
            else ChoiceInteraction(title=record_label(market), actions=actions)
        )
        activity = ActivityRecord(
            activity_id=spec.operation_id,
            kind=ActivityKind.QUERY,
            outcome=ActivityOutcome.SUCCESS,
            title=spec.audit_summary,
            body=body,
            copy_text=renderable_plain_text(body),
            audit_summary=spec.audit_summary,
        )
        return (
            AppendActivity(activity),
            SetInteraction(interaction),
            SetStatus("行情已就绪"),
        )

    if kind is ResultKind.MARKET_ROUTES:
        routes = _route_views(result)
        session.market.routes = routes
        market = session.market.selected
        observation = session.market.observation or "quote"
        if market is None:
            session.enter("market")
            return _choice(
                state,
                session,
                summary=Text("行情上下文已经失效，请重新选择标的。", style="yellow"),
                status="行情上下文已失效",
            )
        preferred = next(
            (route for route in routes if route.provider == session.market.provider),
            None,
        )
        if not routes:
            body = route_diagnostic_renderable(state, market, observation)
            session.context = ("market", "selected")
            session.visible_records = session.market.records
            return (
                _activity(spec, body),
                *_choice(state, session, status="没有可用行情数据源"),
            )
        if preferred is not None or len(routes) == 1:
            provider = (preferred or routes[0]).provider
            return (_run_observation(state, session, provider),)
        session.context = ("market", "providers")
        session.visible_records = selection_records(
            routes,
            key=lambda route: route.provider,
            label=lambda route: route.provider or "未知 Provider",
            description=lambda route: route.description,
        )
        return _choice(state, session, status="请选择行情数据源")

    if kind is ResultKind.MARKET_DATASETS:
        body = Panel(Pretty(result, expand_all=True), title="本地行情数据")
        return (_activity(spec, body), *_choice(state, session, status="数据集已就绪"))

    if kind is ResultKind.MARKET_DIAGNOSTIC:
        session.context = ("market", "selected")
        session.visible_records = session.market.records
        body = Panel(Pretty(result, expand_all=True), title="Market 诊断")
        return (_activity(spec, body), *_choice(state, session, status="诊断已完成"))

    if kind is ResultKind.MARKET_FILE:
        prompt = session.market.file_prompt
        session.market.file_prompt = None
        session.context = ("market", "selected")
        session.visible_records = session.market.records
        body = (
            file_result_renderable(result, prompt)
            if isinstance(prompt, MarketFilePromptState)
            else Panel(Pretty(result, expand_all=True), title="Market 文件操作结果")
        )
        return (
            _activity(spec, body),
            *_choice(state, session, status="文件操作已完成"),
        )

    if kind is ResultKind.WORKSPACE_MARKET:
        prompt = session.market.workspace_prompt
        session.market.workspace_prompt = None
        session.context = ("market", "connected")
        if (
            isinstance(prompt, WorkspaceMarketPromptState)
            and prompt.action == "snapshot"
            and isinstance(result, Mapping)
        ):
            snapshot = dict(result)
            snapshot.setdefault("data_type", prompt.values["kind"])
            snapshot.setdefault("symbol", prompt.values["market-id"])
            snapshot.setdefault("provider", prompt.values["provider"] or "—")
            snapshot["_source_mode"] = "workspace-view"
            snapshot["_fetched_at_unix_nanos"] = time_ns()
            body = observation_renderable(snapshot)
        else:
            body = Panel(Pretty(result, expand_all=True), title="Workspace Market 结果")
        return (_activity(spec, body), *_choice(state, session, status="操作已完成"))

    return None


def handle_failure(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
    error: str,
) -> tuple[ScreenEffect, ...] | None:
    """Restore this product's interaction after a failed finite operation."""

    if spec.route.kind is ResultKind.MARKET:
        _ask_market(session)
        interaction = session.interaction
        assert isinstance(interaction, InputInteraction)
        interaction = InputInteraction(
            action=interaction.action,
            title=interaction.title,
            prompt=interaction.prompt,
            detail=interaction.detail,
            value_summary=interaction.value_summary,
            secret=interaction.secret,
            error=error,
        )
        session.interaction = interaction
        return (
            _activity(spec, Text(error, style="red"), ActivityOutcome.FAILURE),
            SetInteraction(interaction),
            SetStatus("搜索市场失败 · 请重试"),
        )
    if spec.route.kind not in _RESULT_KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
    return (
        _activity(spec, Text(error, style="red"), ActivityOutcome.FAILURE),
        *_choice(
            state,
            session,
            summary=Text(error, style="red"),
            status="操作失败 · 可重试、返回或查看帮助",
        ),
    )


def handle_cancel(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
) -> tuple[ScreenEffect, ...] | None:
    """Restore this product's navigation after cancellation."""

    if spec.route.kind not in _RESULT_KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
    if spec.route.kind is ResultKind.MARKET:
        session.enter("market")
    body = Text("操作在开始执行后被取消。", style="yellow")
    return (
        _activity(spec, body, ActivityOutcome.CANCELLED),
        *_choice(state, session, status="操作已取消 · 可继续输入"),
    )


_RESULT_KINDS = frozenset(
    {
        ResultKind.MARKET,
        ResultKind.MARKET_ROUTES,
        ResultKind.MARKET_OBSERVATION,
        ResultKind.MARKET_DATASETS,
        ResultKind.MARKET_FILE,
        ResultKind.WORKSPACE_MARKET,
        ResultKind.MARKET_DIAGNOSTIC,
    }
)


def _handle_market_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if session.context == ("market", "selected"):
        market = session.market.selected
        if market is None:
            session.enter("market")
            return _choice(state, session, status="行情上下文已失效")
        actions = selected_market_actions(market)
        if session.market.snapshot is not None:
            actions = (*actions, *MARKET_CONTROL_ACTIONS)
        action = action_id(actions, command)
        if action is None:
            return None
        if action == "refresh":
            provider = session.market.provider
            if provider is None:
                return _choice(
                    state,
                    session,
                    summary=Text("尚未选择行情数据源。", style="yellow"),
                    status="无法刷新行情",
                )
            return (_run_observation(state, session, provider),)
        if action == "watch":
            session.market.refresh_enabled = not session.market.refresh_enabled
            interaction = _market_interaction(state, session)
            effects: tuple[ScreenEffect, ...] = (
                SetInteraction(interaction),
                SetStatus(
                    "行情自动刷新中"
                    if session.market.refresh_enabled
                    else "行情自动刷新已暂停"
                ),
            )
            if session.market.refresh_enabled:
                effects = (*effects, RefreshMarketControl())
            return effects
        if action == "save-snapshot":
            snapshot = session.market.snapshot
            if snapshot is None:
                return _choice(
                    state,
                    session,
                    summary=Text("当前没有可保存的行情快照。", style="yellow"),
                    status="没有可保存的行情快照",
                )
            body = snapshot
            summary = f"保存行情快照 · {record_label(market)}"
            return (
                AppendActivity(
                    ActivityRecord(
                        activity_id=str(uuid4()),
                        kind=ActivityKind.QUERY,
                        outcome=ActivityOutcome.SUCCESS,
                        title=summary,
                        body=body,
                        copy_text=renderable_plain_text(body),
                        audit_summary=summary,
                    )
                ),
                SetInteraction(_market_interaction(state, session)),
                SetStatus("当前行情快照已保存到活动历史"),
            )
        if action == "diagnose":
            return (_run_market_diagnostic(state, session),)
        session.market.observation = action
        session.market.snapshot = None
        session.market.refresh_enabled = False
        return (
            RunOperation(
                _spec(
                    action_name=f"market.observe.{action}",
                    summary=f"{record_label(market)} · {action}",
                    route=ResultRoute(ResultKind.MARKET_ROUTES),
                    operation=lambda: load_routes(state, market, action),
                    status="正在查找行情数据源…",
                    equivalent_command=route_command(state, market, action),
                )
            ),
        )

    if session.context == ("market", "connected"):
        action = action_id(WORKSPACE_MARKET_ACTIONS, command)
        if action is None:
            return None
        selected_market = session.market.selected
        default_market = (
            str(getattr(selected_market, "id", ""))
            if selected_market is not None
            else ""
        )
        prompt = WorkspaceMarketPromptState(action, default_market)
        session.market.workspace_prompt = prompt
        return _advance_workspace_prompt(state, session, prompt)

    if len(session.context) > 1 and session.visible_records:
        record = _record_choice(session.visible_records, command)
        if record is None:
            return None
        if session.context[1] == "providers":
            provider = record.provider if isinstance(record, MarketRouteView) else ""
            if not provider:
                return _choice(
                    state,
                    session,
                    summary=Text("所选数据源没有 Provider 标识。", style="yellow"),
                    status="数据源无效",
                )
            return (_run_observation(state, session, provider),)
        session.market.selected = record
        session.market.reset_control()
        session.context = ("market", "selected")
        if session.market.purpose in {"download", "replay"}:
            prompt = MarketFilePromptState(session.market.purpose, record)
            session.market.file_prompt = prompt
            return _advance_file_prompt(state, session, prompt)
        if session.market.purpose == "diagnostics":
            return (_run_market_diagnostic(state, session),)
        return _choice(
            state,
            session,
            status=f"已选择 {record_label(record)} · 请选择行情",
        )

    action = action_id((*SECTION_ACTIONS["market"], *MARKET_ADVANCED_ACTIONS), command)
    if action is None:
        return None
    if action in {"search", "download", "replay", "diagnostics", "advanced"}:
        session.market.purpose = action
        return _ask_market(session)
    if action == "datasets":
        return (
            RunOperation(
                _spec(
                    action_name="market.datasets",
                    summary="查看本地行情数据集",
                    route=ResultRoute(ResultKind.MARKET_DATASETS),
                    operation=lambda: load_datasets(state),
                    status="正在读取本地行情数据集…",
                )
            ),
        )
    if action == "connected":
        session.context = ("market", "connected")
        return _choice(state, session)
    return None


def _ask_market(session: GuidedSession) -> tuple[ScreenEffect, ...]:
    prompt, detail = _market_prompt_copy(session.market.purpose)
    title = {
        "search": "搜索市场",
        "download": "选择历史行情标的",
        "replay": "选择回放标的",
        "diagnostics": "选择待诊断标的",
        "advanced": "高级市场标识",
    }.get(session.market.purpose, "搜索市场")
    session.ask(
        ActionToken(Feature.MARKET, "search"),
        title=title,
        prompt=prompt,
        detail=detail,
    )
    return SetInteraction(session.interaction), SetStatus(f"{title} · 等待输入")


def _market_prompt_copy(purpose: str) -> tuple[str, str]:
    return {
        "search": ("输入代码或名称", "例如 AAPL、比特币或 BTCUSDT。"),
        "download": ("输入要下载的标的", "可输入代码、名称或完整 Market ID。"),
        "replay": ("输入要回放的标的", "可输入代码、名称或完整 Market ID。"),
        "diagnostics": ("输入要诊断的标的", "可输入代码、名称或完整 Market ID。"),
        "advanced": ("输入完整 Market ID", "使用高级市场标识精确定位标的。"),
    }.get(purpose, ("输入代码或名称", "例如 AAPL、比特币或 BTCUSDT。"))


def _run_market_search(state: Any, session: GuidedSession, query: str) -> RunOperation:
    return RunOperation(
        _spec(
            action_name="market.find",
            summary=f"搜索市场标的 · {query}",
            route=ResultRoute(ResultKind.MARKET),
            operation=lambda: load_records(state, "markets", query),
            status="正在搜索市场标的…",
        )
    )


def _run_observation(state: Any, session: GuidedSession, provider: str) -> RunOperation:
    market = session.market.selected
    observation = session.market.observation
    assert market is not None and observation is not None
    session.market.provider = provider
    return RunOperation(
        _spec(
            action_name=f"market.observe.{observation}",
            summary=f"{record_label(market)} · {observation} · {provider}",
            route=ResultRoute(ResultKind.MARKET_OBSERVATION),
            operation=lambda: load_observation(state, market, observation, provider),
            status=f"正在通过 {provider} 读取行情…",
            equivalent_command=observation_command(
                state, market, observation, provider
            ),
        )
    )


def _run_market_diagnostic(state: Any, session: GuidedSession) -> RunOperation:
    market = session.market.selected
    assert market is not None
    return RunOperation(
        _spec(
            action_name="market.diagnose",
            summary=f"{record_label(market)} · 市场诊断",
            route=ResultRoute(ResultKind.MARKET_DIAGNOSTIC),
            operation=lambda: run_diagnostic(state, market),
            status="正在诊断当前市场…",
            equivalent_command=diagnostic_command(state, market),
        )
    )


def _accept_prompt_field(
    state: Any,
    session: GuidedSession,
    prompt: MarketFilePromptState | WorkspaceMarketPromptState,
    field_name: str,
    raw: str,
    *,
    workspace: bool,
) -> tuple[ScreenEffect, ...]:
    try:
        prompt.accept(field_name, raw)
    except ValueError as error:
        current = session.interaction
        if isinstance(current, InputInteraction):
            interaction = InputInteraction(
                action=current.action,
                title=current.title,
                prompt=current.prompt,
                detail=current.detail,
                value_summary=current.value_summary,
                secret=current.secret,
                error=str(error),
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("输入有误 · 请修正")
        return _choice(
            state, session, summary=Text(str(error), style="yellow"), status="输入有误"
        )
    if workspace:
        assert isinstance(prompt, WorkspaceMarketPromptState)
        return _advance_workspace_prompt(state, session, prompt)
    assert isinstance(prompt, MarketFilePromptState)
    return _advance_file_prompt(state, session, prompt)


def _advance_file_prompt(
    state: Any, session: GuidedSession, prompt: MarketFilePromptState
) -> tuple[ScreenEffect, ...]:
    next_prompt = prompt.next_prompt()
    if next_prompt is not None:
        name, label, detail = next_prompt
        session.ask(
            ActionToken(Feature.MARKET, "file-field", name),
            title="Market 文件操作",
            prompt=label,
            detail=detail,
            value_summary=Pretty(prompt.summary(), expand_all=True),
        )
        return SetInteraction(session.interaction), SetStatus("等待 Market 文件参数")

    def operation() -> Any:
        if state.dry_run or state.no_exec:
            return preview_file_action(prompt)
        return execute_file_action(state, prompt)

    spec = _spec(
        action_name=f"market.file.{prompt.action}",
        summary=f"Market {prompt.action} {prompt.market.id}",
        route=ResultRoute(ResultKind.MARKET_FILE),
        operation=operation,
        status=f"正在执行 Market {prompt.action}…",
        equivalent_command=file_command(state, prompt),
    )
    return _confirm_or_run(
        state,
        session,
        spec,
        title="Market 文件操作范围",
        details=Pretty(prompt.summary(), expand_all=True),
        dangerous=True,
    )


def _advance_workspace_prompt(
    state: Any, session: GuidedSession, prompt: WorkspaceMarketPromptState
) -> tuple[ScreenEffect, ...]:
    next_prompt = prompt.next_prompt()
    if next_prompt is not None:
        name, label, detail = next_prompt
        session.ask(
            ActionToken(Feature.MARKET, "workspace-field", name),
            title="Workspace Market",
            prompt=label,
            detail=detail,
            value_summary=Pretty(prompt.summary(), expand_all=True),
        )
        return SetInteraction(session.interaction), SetStatus(
            "等待 Workspace Market 参数"
        )

    def operation() -> Any:
        if state.dry_run or state.no_exec:
            return preview_workspace_market(prompt)
        return execute_workspace_market(state, prompt)

    spec = _spec(
        action_name=f"workspace.market.{prompt.action}",
        summary=f"Workspace Market {prompt.action}",
        route=ResultRoute(ResultKind.WORKSPACE_MARKET),
        operation=operation,
        status=f"正在执行 Workspace Market {prompt.action}…",
        equivalent_command=workspace_market_command(state, prompt),
    )
    return _confirm_or_run(
        state,
        session,
        spec,
        title="Workspace Market 操作确认",
        details=Pretty(prompt.summary(), expand_all=True),
        dangerous=prompt.dangerous,
    )


def _confirm_or_run(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
    *,
    title: str,
    details: Any,
    dangerous: bool,
) -> tuple[ScreenEffect, ...]:
    if not dangerous or state.yes or state.dry_run or state.no_exec:
        return (RunOperation(spec),)
    session.confirm(spec, title=title, display_summary=details)
    return SetInteraction(session.interaction), SetStatus("等待确认")


def _choice(
    state: Any,
    session: GuidedSession,
    *,
    summary: Any | None = None,
    status: str = "就绪",
) -> tuple[ScreenEffect, ...]:
    interaction = _market_interaction(state, session, summary=summary)
    session.interaction = interaction
    return SetInteraction(interaction), SetStatus(status)


def _market_interaction(
    state: Any, session: GuidedSession, *, summary: Any | None = None
) -> ChoiceInteraction | ControlInteraction:
    if (
        session.context == ("market", "selected")
        and session.market.snapshot is not None
        and session.market.refresh_enabled
        and session.market.selected is not None
    ):
        return ControlInteraction(
            title=record_label(session.market.selected),
            snapshot=session.market.snapshot,
            actions=context_items(session, state),
            refreshing=True,
        )
    return ChoiceInteraction(
        title=context_label(session.context),
        summary=summary,
        actions=context_items(session, state),
    )


def _record_choice(records: tuple[SelectionRecord, ...], value: str) -> object | None:
    return selected_value(records, value)


def _spec(
    *,
    action_name: str,
    summary: str,
    route: ResultRoute,
    operation: Any,
    status: str,
    equivalent_command: tuple[str, ...] | None = None,
) -> OperationSpec:
    return OperationSpec.create(
        action_name=action_name,
        audit_summary=summary,
        route=route,
        operation=operation,
        running_status=status,
        equivalent_command=equivalent_command,
    )


def _activity(
    spec: OperationSpec,
    body: Any,
    outcome: ActivityOutcome = ActivityOutcome.SUCCESS,
) -> AppendActivity:
    return AppendActivity(
        ActivityRecord(
            activity_id=spec.operation_id,
            kind=ActivityKind.QUERY,
            outcome=outcome,
            title=spec.audit_summary,
            body=body,
            copy_text=renderable_plain_text(body),
            audit_summary=spec.audit_summary,
        )
    )


def _standalone_activity(title: str, body: Any) -> AppendActivity:
    return AppendActivity(
        ActivityRecord(
            activity_id=f"activity-{uuid4().hex[:12]}",
            kind=ActivityKind.QUERY,
            outcome=ActivityOutcome.SUCCESS,
            title=title,
            body=body,
            copy_text=renderable_plain_text(body),
            audit_summary=title,
        )
    )


def _show_record_choices(
    session: GuidedSession,
    section: str,
    records: tuple[Any, ...],
    *,
    record_kind: str | None = None,
) -> tuple[ScreenEffect, ...]:
    session.context = (section, record_kind or "results")
    visible = _visible(records)
    session.visible_records = visible
    actions = tuple(
        ActionItem(
            str(index),
            record.label,
            record.description,
            str(index),
        )
        for index, record in enumerate(visible, 1)
    )
    interaction = ChoiceInteraction(
        title=("首页 / 市场行情" if section == "market" else "首页 / 市场标的"),
        actions=actions,
    )
    return (
        SetInteraction(interaction),
        SetStatus(f"找到 {len(records)} 个结果 · 请选择"),
    )


def _visible(records: tuple[Any, ...]) -> tuple[SelectionRecord, ...]:
    return selection_records(
        records,
        label=record_label,
        description=record_description,
    )


def _route_views(result: Any) -> tuple[MarketRouteView, ...]:
    routes: list[MarketRouteView] = []
    for route in result or ():
        if isinstance(route, MarketRouteView):
            routes.append(route)
        elif isinstance(route, Mapping):
            routes.append(MarketRouteView.from_mapping(route))
    return tuple(routes)


__all__ = [
    "handle_cancel",
    "handle_command",
    "handle_context",
    "handle_failure",
    "handle_success",
]
